#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indexer_unified.py — UNIFIED HTML + PDF + DOCX INDEXER (LlamaParse API v2)

What changed vs your current indexer:
  ✅ Adds DOCX ingestion
  ✅ Adds a built-in LlamaParse v2 client (no separate pdf_parser.py needed)
  ✅ Keeps your existing HTML pipeline intact (indexer_section_wise.py unchanged)
  ✅ Incremental indexing still works (manifest + chunks.jsonl + Chroma "docs" collection)
  ✅ Safer + more debuggable parsing cache: stores raw v2 job JSON + markdown in index_store/llamaparse_cache/

Environment:
  - LLAMA_CLOUD_API_KEY   (required if you parse PDFs/DOCX via LlamaParse v2)
  - EMBED_MODEL           (default: text-embedding-3-large)
  - EMBED_BATCH           (default: 256)

Usage:
  python tools/indexer_unified.py --client_root "D:/sms-copilot/rsms" --only_approved
  python tools/indexer_unified.py --client_root "D:/sms-copilot/rsms" --force_reindex

Notes:
  - LlamaParse v2 is tier-based (fast | cost_effective | agentic | agentic_plus).
  - For best PDF layout + page fidelity, set --llamaparse_tier agentic (or agentic_plus for very complex docs).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import chromadb
import requests

from llama_index.core import Settings
from llama_index.core.schema import TextNode
from llama_index.core.storage import StorageContext
from llama_index.core.indices.vector_store import VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# Your existing HTML pipeline (UNCHANGED)
from indexer_section_wise import (
    load_manifest,
    save_manifest,
    file_hash,
    load_viq_rules,
    load_synonyms,
    enrich_chunks,
    build_sections_from_html,
    build_chunks_from_sections,
    stable_slug,
    stable_node_id,
    build_breadcrumb,
    print_integrity_report as _html_integrity_report,
)

try:
    from tqdm import tqdm  # optional
    HAS_TQDM = True
except Exception:
    HAS_TQDM = False


# =========================
# Logging
# =========================
log = logging.getLogger("indexer_unified")


# =========================
# LlamaParse v2 Client
# =========================

LLAMAPARSE_V2_UPLOAD_URL = "https://api.cloud.llamaindex.ai/api/v2/parse/upload"
LLAMAPARSE_V2_JOB_URL = "https://api.cloud.llamaindex.ai/api/v2/parse/{job_id}"

_TERMINAL_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}


@dataclass(frozen=True)
class LlamaParseV2Config:
    """Minimal v2 configuration that works well for RAG."""
    tier: str = "agentic"            # fast | cost_effective | agentic | agentic_plus
    version: str = "latest"          # or fixed version string like "2026-01-08"
    disable_cache: bool = False      # when True: forces fresh parse
    # Output options tuned for RAG: keep links, good tables, allow printed page extraction
    annotate_links: bool = True
    merge_continued_tables: bool = True
    extract_printed_page_number: bool = True
    # Processing control (timeouts)
    base_timeout_s: int = 300
    extra_timeout_per_page_s: int = 30


class LlamaParseV2Error(RuntimeError):
    pass


def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _lp_cache_paths(cache_dir: Path, file_path: Path, cfg: LlamaParseV2Config) -> Dict[str, Path]:
    # Cache key: file content hash + tier+version+disable_cache flag
    key = f"{_sha256_file(file_path)}|tier={cfg.tier}|ver={cfg.version}|nocache={int(cfg.disable_cache)}"
    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    stem = f"{file_path.stem}__{key_hash}"
    return {
        "job": cache_dir / f"{stem}.job.json",
        "result": cache_dir / f"{stem}.result.json",
        "markdown": cache_dir / f"{stem}.md",
    }


def llamaparse_v2_upload_and_wait(
    *,
    file_path: Path,
    api_key: str,
    cfg: LlamaParseV2Config,
    cache_dir: Path,
    poll_interval_s: float = 1.5,
    max_wait_s: int = 1800,
) -> Dict[str, Any]:
    """
    Uploads a file to LlamaParse v2 (/parse/upload) and polls until terminal state.

    Returns the final GET /parse/{job_id}?expand=... JSON (includes 'job' plus optionally
    'markdown', 'text', 'items', 'metadata', etc depending on expand).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_paths = _lp_cache_paths(cache_dir, file_path, cfg)

    # Fast path: reuse cached result if present and disable_cache is False
    if cache_paths["result"].exists() and cache_paths["markdown"].exists() and not cfg.disable_cache:
        try:
            result = json.loads(cache_paths["result"].read_text(encoding="utf-8"))
            log.info(f"✅ Using cached LlamaParse result for {file_path.name} — no upload to LlamaCloud")
            return result
        except Exception:
            # fall through to re-parse
            pass

    if not api_key:
        raise LlamaParseV2Error("LLAMA_CLOUD_API_KEY is missing (required for PDF/DOCX parsing).")

    headers = {"Authorization": f"Bearer {api_key}"}

    configuration = {
        "tier": cfg.tier,
        "version": cfg.version,
        "disable_cache": bool(cfg.disable_cache),
        "output_options": {
            "markdown": {
                "annotate_links": bool(cfg.annotate_links),
                "tables": {
                    "merge_continued_tables": bool(cfg.merge_continued_tables),
                },
            },
            "extract_printed_page_number": bool(cfg.extract_printed_page_number),
        },
        "processing_control": {
            "timeouts": {
                "base_in_seconds": int(cfg.base_timeout_s),
                "extra_time_per_page_in_seconds": int(cfg.extra_timeout_per_page_s),
            }
        },
    }

    files = {"file": (file_path.name, file_path.open("rb"), "application/octet-stream")}
    data = {"configuration": _safe_json_dumps(configuration)}

    # Upload
    log.info(f"☁️  Uploading {file_path.name} to LlamaParse v2 (tier={cfg.tier}) — document sent to LlamaCloud servers for parsing")
    resp = requests.post(LLAMAPARSE_V2_UPLOAD_URL, headers=headers, files=files, data=data, timeout=120)
    try:
        resp.raise_for_status()
    except Exception as e:
        raise LlamaParseV2Error(f"LlamaParse v2 upload failed: HTTP {resp.status_code} — {resp.text[:500]}") from e

    upload_json = resp.json()
    cache_paths["job"].write_text(_safe_json_dumps(upload_json), encoding="utf-8")

    job_id = upload_json.get("id") or upload_json.get("job_id")
    if not job_id:
        raise LlamaParseV2Error(f"Unexpected upload response (missing job id): {upload_json}")

    # Poll for results (ask for markdown + metadata + items — gives you max flexibility)
    deadline = time.time() + max_wait_s
    last_status = None

    expand = "markdown,metadata,items"
    while True:
        if time.time() > deadline:
            raise LlamaParseV2Error(f"Timed out waiting for LlamaParse job {job_id} after {max_wait_s}s")

        url = LLAMAPARSE_V2_JOB_URL.format(job_id=job_id)
        r = requests.get(url, headers=headers, params={"expand": expand}, timeout=120)
        try:
            r.raise_for_status()
        except Exception as e:
            raise LlamaParseV2Error(f"Failed to fetch LlamaParse job {job_id}: HTTP {r.status_code} — {r.text[:500]}") from e

        result = r.json()
        job = result.get("job") or {}
        status = (job.get("status") or "").upper()

        if status and status != last_status:
            log.info(f"   - LlamaParse job {job_id}: {status}")
            last_status = status

        if status in _TERMINAL_STATUSES:
            if status != "COMPLETED":
                err = job.get("error_message") or "Unknown error"
                raise LlamaParseV2Error(f"LlamaParse job {job_id} ended with {status}: {err}")

            # Save caches
            cache_paths["result"].write_text(_safe_json_dumps(result), encoding="utf-8")
            # v2 returns markdown as {"pages": [{"page_number":int,"markdown":str}, ...]}
            md_raw = result.get("markdown") or ""
            if isinstance(md_raw, dict):
                pages = md_raw.get("pages", [])
                full_md = "\n\n".join(
                    p.get("markdown", "") for p in pages
                    if isinstance(p, dict) and p.get("markdown")
                )
            else:
                full_md = str(md_raw)
            cache_paths["markdown"].write_text(full_md, encoding="utf-8")
            return result

        time.sleep(poll_interval_s)


# =========================
# Markdown → Chunks
# =========================

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)\s*$", re.MULTILINE)


def _split_by_headings(md: str) -> List[Tuple[str, str]]:
    """
    Returns [(title, content_md)].
    If no headings exist, returns [("Document", md)].
    """
    matches = list(_HEADING_RE.finditer(md))
    if not matches:
        return [("Document", md)]

    sections: List[Tuple[str, str]] = []
    for idx, m in enumerate(matches):
        title = m.group(2).strip() or "Section"
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md)
        sections.append((title, md[start:end].strip()))
    # If there is content before first heading, keep it too
    prefix = md[:matches[0].start()].strip()
    if prefix:
        sections.insert(0, ("Preamble", prefix))
    return sections


def _chunk_text_by_chars(text: str, max_chars: int, overlap_chars: int) -> List[str]:
    """
    Simple deterministic chunker:
      - max_chars is an approximate chunk size target
      - overlap_chars preserves some trailing context between chunks
    """
    text = (text or "").strip()
    if not text:
        return []

    if max_chars <= 0:
        return [text]

    chunks = []
    i = 0
    n = len(text)
    while i < n:
        j = min(i + max_chars, n)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j >= n:
            break
        i = max(0, j - max(0, overlap_chars))
    return chunks


def _pages_from_items(items: Any) -> Dict[int, str]:
    """
    Best-effort extraction of per-page markdown/text from structured 'items' output.
    v2's exact item schema can vary; we handle common patterns defensively.

    Returns {page_number: page_text}
    """
    pages: Dict[int, List[str]] = {}

    if not isinstance(items, list):
        return {}

    for it in items:
        if not isinstance(it, dict):
            continue
        # try common field names
        page = (
            it.get("page")
            or it.get("page_number")
            or it.get("page_num")
            or it.get("pageIndex")
            or it.get("page_index")
        )
        try:
            page_int = int(page)
        except Exception:
            continue

        # prefer markdown-like field names
        payload = (
            it.get("md")
            or it.get("markdown")
            or it.get("text")
            or it.get("content")
            or ""
        )
        payload = str(payload).strip()
        if not payload:
            continue

        pages.setdefault(page_int, []).append(payload)

    return {p: "\n\n".join(parts).strip() for p, parts in pages.items() if parts}


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags (e.g. <u>, <b>, <i>, <mark>) from LlamaParse section titles."""
    if not text:
        return text
    return re.sub(r'<[^>]+>', '', text).strip()


def build_chunks_from_llamaparse_markdown(
    *,
    md: str,
    source_file: str,
    source_type: str,  # pdf|docx|md
    max_chunk_size: int,
    chunk_overlap: int,
    extra_metadata: Optional[Dict[str, Any]] = None,
    page_map: Optional[Dict[int, str]] = None,
) -> List[TextNode]:
    """
    Converts LlamaParse markdown into TextNodes with metadata.
    If page_map is provided, chunks are produced per-page (better citations).
    """
    extra_metadata = extra_metadata or {}
    nodes: List[TextNode] = []

    def add_node(text: str, section_title: str, page_number: Optional[int], chunk_index: int) -> None:
        page = int(page_number) if page_number is not None else 0

        # Strip HTML tags from LlamaParse section titles (e.g. <u>, <b>, <mark>)
        clean_title = _strip_html_tags(section_title)

        # Issue 6: Retrieval code requires exactly: file, slug_url, breadcrumb, section_title
        if source_type == "pdf":
            slug_url = f"{source_file}#page={page}" if page else source_file
            breadcrumb = f"{clean_title} (p.{page})" if page else clean_title
        else:
            # docx or other — heading-based anchor like HTML
            slug_url = f"{source_file}#{stable_slug(clean_title)}"
            breadcrumb = clean_title

        meta = {
            "file": source_file,                 # retrieval field 1
            "slug_url": slug_url,                # retrieval field 2
            "breadcrumb": build_breadcrumb(
                Path(source_file).stem, breadcrumb
            ),                                   # retrieval field 3
            "section_title": clean_title,        # retrieval field 4
            "source_type": source_type,
            "chunk_index": chunk_index,
        }
        if page_number is not None:
            meta["page_number"] = page
        meta.update(extra_metadata)

        # Stable-ish chunk id: source + section + page + index + sha1(text)
        h = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:12]
        base = f"{source_file}|{source_type}|{section_title}|p={page}|i={chunk_index}|{h}"
        chunk_id = hashlib.sha1(base.encode("utf-8")).hexdigest()

        nodes.append(TextNode(id_=chunk_id, text=text, metadata=meta))

    if page_map:
        for page_no in sorted(page_map.keys()):
            page_text = page_map[page_no]
            sections = _split_by_headings(page_text) or [("Page", page_text)]
            local_idx = 0
            for title, body in sections:
                for piece in _chunk_text_by_chars(body, max_chunk_size, chunk_overlap):
                    add_node(piece, title, page_no, local_idx)
                    local_idx += 1
        return nodes

    # no page map: chunk document-level sections
    sections = _split_by_headings(md)
    idx = 0
    for title, body in sections:
        for piece in _chunk_text_by_chars(body, max_chunk_size, chunk_overlap):
            add_node(piece, title, None, idx)
            idx += 1
    return nodes


# =========================
# Main indexer
# =========================

def _create_stub_chunk(source_file: str, source_type: str) -> TextNode:
    """Create a single title-stub chunk for image-only files (Issue 5)."""
    doc_title = Path(source_file).stem.replace("_", " ")
    stub_id = stable_slug(doc_title)
    if source_type == "pdf":
        slug_url = source_file
    else:
        slug_url = f"{source_file}#{stub_id}"
    node = TextNode(
        text=doc_title,
        metadata={
            "file": source_file,
            "slug_url": slug_url,
            "breadcrumb": build_breadcrumb(Path(source_file).stem, doc_title),
            "section_title": doc_title,
            "source_type": source_type,
            "chunk_index": 0,
            "is_complete_section": True,
            "stub": True,
        },
    )
    node.id_ = stable_node_id(source_file, stub_id, 0)
    return node


# =========================
# Unified Integrity Report
# =========================

def print_unified_integrity_report(manifest: dict, chroma_collection, docs_dir: Path):
    """Cross-check ALL file types (html/pdf/docx) against ChromaDB."""
    log.info("=" * 60)
    log.info("📊 INTEGRITY REPORT")
    log.info("=" * 60)

    all_files = (
        sorted(docs_dir.glob("*.html"))
        + sorted(docs_dir.glob("*.pdf"))
        + sorted(docs_dir.glob("*.docx"))
        + sorted(docs_dir.glob("*.doc"))
    )
    total_files = len(all_files)
    not_in_manifest = []
    missing_from_chroma = []
    partial_in_chroma = []
    image_only_stubs = []
    fully_confirmed = 0

    for file_path in all_files:
        entry = manifest["files"].get(file_path.name)
        if not entry:
            not_in_manifest.append(file_path.name)
            continue

        expected_chunks = entry.get("chunks", 0)
        is_stub = entry.get("stub", False)

        try:
            file_results = chroma_collection.get(
                where={"file": file_path.name},
                include=["metadatas"],
            )
            actual_count = len(file_results["ids"])
        except Exception:
            actual_count = 0

        if is_stub and actual_count >= 1:
            image_only_stubs.append(file_path.name)
        elif actual_count == 0:
            missing_from_chroma.append((file_path.name, expected_chunks, 0))
        elif actual_count < expected_chunks:
            partial_in_chroma.append((file_path.name, expected_chunks, actual_count))
        else:
            fully_confirmed += 1

    log.info(f"Total files in documents folder      : {total_files}")
    log.info(f"Files recorded in manifest           : {total_files - len(not_in_manifest)}")
    log.info(f"Files fully confirmed in ChromaDB    : {fully_confirmed}")
    log.info(f"Image-only files (title stub)        : {len(image_only_stubs)}")

    if image_only_stubs:
        log.info(f"\n🖼️  IMAGE-ONLY (title stub) — no extractable text ({len(image_only_stubs)} files):")
        for f in image_only_stubs:
            log.info(f"     - {f}")

    if not_in_manifest:
        log.warning(f"\n⚠️  NEVER PROCESSED — not in manifest ({len(not_in_manifest)} files):")
        for f in not_in_manifest:
            log.warning(f"     - {f}")

    if missing_from_chroma:
        log.error(f"\n❌ IN MANIFEST BUT ZERO CHUNKS IN CHROMADB ({len(missing_from_chroma)} files):")
        for fname, expected, actual in missing_from_chroma:
            log.error(f"     - {fname}  (manifest says {expected} chunks, ChromaDB has {actual})")

    if partial_in_chroma:
        log.warning(f"\n⚠️  PARTIAL — fewer chunks than expected ({len(partial_in_chroma)} files):")
        for fname, expected, actual in partial_in_chroma:
            log.warning(f"     - {fname}  (expected {expected}, found {actual})")

    if not missing_from_chroma and not partial_in_chroma and not not_in_manifest:
        log.info("\n✅ All files confirmed — manifest and ChromaDB are fully in sync.")
    else:
        total_problem_files = len(not_in_manifest) + len(missing_from_chroma) + len(partial_in_chroma)
        if total_problem_files > 0:
            log.error(f"\n⚠️  ACTION REQUIRED: {total_problem_files} files have indexing gaps.")
            log.error("   Run with --force_reindex flag to fix all missing embeddings.")

    log.info("=" * 60)


# =========================
# Main indexer
# =========================

def index_client(
    *,
    client_root: Path,
    rules_path: Optional[Path] = None,
    force_reindex: bool = False,
    max_chunk_size: int = 1200,
    chunk_overlap: int = 150,
    parallel_workers: int = 4,
    llamaparse_api_key: str = "",
    llamaparse_tier: str = "agentic",
    llamaparse_version: str = "latest",
    llamaparse_disable_cache: bool = False,
) -> None:
    """
    Runs indexing for a client directory that contains:
      client_root/
        documents/            (html/pdf/docx)   ← Issue 2: hardcoded
        index_store/          (manifest.json, chunks.jsonl, chroma/, etc)
    """
    client_root = client_root.resolve()
    docs_dir = client_root / "documents"                   # Issue 2: always documents/
    store_dir = client_root / "index_store"
    chroma_dir = store_dir / "chroma"
    manifest_file = store_dir / "manifest.json"
    chunks_file = store_dir / "chunks.jsonl"
    settings_file = store_dir / "settings.json"
    llama_cache_dir = store_dir / "llamaparse_cache"

    store_dir.mkdir(parents=True, exist_ok=True)
    chroma_dir.mkdir(parents=True, exist_ok=True)
    llama_cache_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Client root: {client_root}")
    log.info(f"Docs dir:    {docs_dir}")
    log.info(f"Store dir:   {store_dir}")
    log.info(f"LlamaParse:  tier={llamaparse_tier}, version={llamaparse_version}, disable_cache={llamaparse_disable_cache}")

    if not docs_dir.exists():
        raise FileNotFoundError(f"documents directory not found: {docs_dir}")

    # Load VIQ rules and synonyms (used by existing HTML chunker)
    _rules_path = rules_path or (client_root / "rules")
    viq_rules = load_viq_rules(_rules_path)
    synonyms = load_synonyms(_rules_path)

    # Load manifest for incremental updates
    manifest = load_manifest(manifest_file)

    # ========== FIND DOCUMENTS TO PROCESS ==========
    html_files = sorted(docs_dir.glob("*.html"))
    pdf_files = sorted(docs_dir.glob("*.pdf"))
    docx_files = sorted(docs_dir.glob("*.docx")) + sorted(docs_dir.glob("*.doc"))

    log.info(f"[SCAN] Found {len(html_files)} HTML, {len(pdf_files)} PDF, {len(docx_files)} DOCX/DOC")

    def needs_processing(p: Path) -> bool:
        prev = manifest["files"].get(p.name, {})
        curr_hash = file_hash(p)
        # Issue 4: auto-retry files with 0 chunks or not embedded
        needs_retry = prev.get("chunks", 0) == 0 or not prev.get("embedded", False)
        return force_reindex or prev.get("sha256") != curr_hash or needs_retry

    html_to_process = [p for p in html_files if needs_processing(p)]
    pdf_to_process = [p for p in pdf_files if needs_processing(p)]
    docx_to_process = [p for p in docx_files if needs_processing(p)]

    log.info(f"[PROCESS] {len(html_to_process)} HTML files to process")
    log.info(f"[PROCESS] {len(pdf_to_process)} PDF files to process")
    log.info(f"[PROCESS] {len(docx_to_process)} DOCX files to process")

    if not html_to_process and not pdf_to_process and not docx_to_process:
        log.info("✅ No files to process. Index is up to date.")
        # Still run integrity report
        db = chromadb.PersistentClient(path=str(chroma_dir))
        chroma_collection = db.get_or_create_collection("docs")
        print_unified_integrity_report(manifest, chroma_collection, docs_dir)
        return

    # ========== STAGE 1: CHUNKING (collect into manifest_updates, NOT manifest) ==========
    all_chunks: List[TextNode] = []
    chunks_per_file: Dict[str, List[str]] = {}      # filename -> [chunk_id, ...]
    manifest_updates: Dict[str, dict] = {}           # filename -> manifest entry (Issue 3)

    # --- HTML FILES ---
    if html_to_process:
        log.info(f"\n📄 Processing {len(html_to_process)} HTML files...")
        for file_path in html_to_process:
            try:
                sections = build_sections_from_html(file_path=file_path)
                file_chunks = build_chunks_from_sections(
                    sections=sections,
                    source_file=file_path.name,
                    source_type="html",
                    max_chunk_size=max_chunk_size,
                    min_chunk_size=100,
                    overlap=chunk_overlap,
                )

                # Issue 5: image-only HTML stub
                is_stub = False
                if not file_chunks:
                    stub = _create_stub_chunk(file_path.name, "html")
                    file_chunks = [stub]
                    is_stub = True
                    log.warning(f"⚠️  Image-only file — created title stub: {file_path.name}")

                all_chunks.extend(file_chunks)
                chunks_per_file[file_path.name] = [c.id_ for c in file_chunks]
                manifest_updates[file_path.name] = {
                    "type": "html",
                    "sha256": file_hash(file_path),
                    "mtime": file_path.stat().st_mtime,
                    "sections": len(sections),
                    "chunks": len(file_chunks),
                    "stub": is_stub,
                }
                log.info(f"✅ HTML: {file_path.name} → {len(sections)} sections → {len(file_chunks)} chunks{' (title stub)' if is_stub else ''}")
            except Exception as e:
                log.exception(f"❌ Failed to process HTML {file_path.name}: {e}")

    # --- PDF & DOCX via LlamaParse v2 ---
    lp_cfg = LlamaParseV2Config(
        tier=llamaparse_tier,
        version=llamaparse_version,
        disable_cache=llamaparse_disable_cache,
    )

    def _extract_v2_markdown(result: dict) -> Tuple[str, Optional[Dict[int, str]]]:
        """Extract full markdown text and page_map from LlamaParse v2 response."""
        md_raw = result.get("markdown") or ""
        page_map: Optional[Dict[int, str]] = None

        if isinstance(md_raw, dict):
            # v2 format: {"pages": [{"page_number":int, "markdown":str, "success":bool}]}
            pages = md_raw.get("pages", [])
            page_map = {}
            parts = []
            for p in pages:
                if not isinstance(p, dict):
                    continue
                pg_num = p.get("page_number", 0)
                pg_md = p.get("markdown", "")
                if pg_md:
                    parts.append(pg_md)
                    page_map[pg_num] = pg_md
            full_md = "\n\n".join(parts)
            if not page_map:
                page_map = None
        else:
            full_md = str(md_raw)
            # try items for page_map
            items = result.get("items")
            if isinstance(items, dict):
                items = items.get("pages", [])
            page_map = _pages_from_items(items) if items else None

        return full_md, page_map

    def process_with_llamaparse(path: Path, source_type: str) -> List[TextNode]:
        result = llamaparse_v2_upload_and_wait(
            file_path=path,
            api_key=llamaparse_api_key,
            cfg=lp_cfg,
            cache_dir=llama_cache_dir,
        )
        full_md, page_map = _extract_v2_markdown(result)

        nodes = build_chunks_from_llamaparse_markdown(
            md=full_md,
            source_file=path.name,
            source_type=source_type,
            max_chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
            extra_metadata={
                "llamaparse_tier": lp_cfg.tier,
                "llamaparse_version": lp_cfg.version,
            },
            page_map=page_map,
        )
        return nodes

    for file_list, src_type in [(pdf_to_process, "pdf"), (docx_to_process, "docx")]:
        if not file_list:
            continue
        log.info(f"\n📄 Processing {len(file_list)} {src_type.upper()} files (LlamaParse v2)...")
        for fpath in file_list:
            try:
                file_chunks = process_with_llamaparse(fpath, src_type)

                # Issue 5: image-only stub for PDF/DOCX
                is_stub = False
                if not file_chunks:
                    stub = _create_stub_chunk(fpath.name, src_type)
                    file_chunks = [stub]
                    is_stub = True
                    log.warning(f"⚠️  Image-only file — created title stub: {fpath.name}")

                all_chunks.extend(file_chunks)
                chunks_per_file[fpath.name] = [c.id_ for c in file_chunks]
                manifest_updates[fpath.name] = {
                    "type": src_type,
                    "sha256": file_hash(fpath),
                    "mtime": fpath.stat().st_mtime,
                    "chunks": len(file_chunks),
                    "stub": is_stub,
                    "llamaparse_tier": lp_cfg.tier,
                    "llamaparse_version": lp_cfg.version,
                }
                log.info(f"✅ {src_type.upper()}: {fpath.name} → {len(file_chunks)} chunks{' (title stub)' if is_stub else ''}")
            except Exception as e:
                log.exception(f"❌ Failed to process {src_type.upper()} {fpath.name}: {e}")

    log.info(f"\nTotal chunks created this run: {len(all_chunks)}")

    # VIQ / synonym enrichment on all chunks
    if viq_rules or synonyms:
        log.info("Enriching chunks with VIQ and synonym metadata...")
        all_chunks = enrich_chunks(all_chunks, viq_rules, synonyms)

    # Save chunks.jsonl
    log.info(f"💾 Saving {len(all_chunks)} chunks to {chunks_file}")
    with chunks_file.open("w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps({
                "id_": chunk.id_,
                "text": chunk.text,
                "metadata": chunk.metadata,
            }, ensure_ascii=False) + "\n")

    # ========== STAGE 2: EMBEDDING ==========
    log.info("\n🔄 Setting up embeddings...")
    Settings.embed_model = OpenAIEmbedding(
        model=os.getenv("EMBED_MODEL", "text-embedding-3-large"),
        embed_batch_size=int(os.getenv("EMBED_BATCH", "256")),
        timeout=60,
    )

    db = chromadb.PersistentClient(path=str(chroma_dir))
    chroma_collection = db.get_or_create_collection("docs")
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    existing_ids = set(chroma_collection.get().get("ids", []))
    new_chunks = [c for c in all_chunks if c.id_ not in existing_ids]

    if new_chunks:
        log.info(f"🔄 Chroma has {len(existing_ids)} chunks. Embedding {len(new_chunks)} new chunks...")
        batch_size = int(os.getenv("EMBED_BATCH", "256"))
        batches = [new_chunks[i:i + batch_size] for i in range(0, len(new_chunks), batch_size)]
        log.info(f"📦 Processing {len(batches)} batches with {parallel_workers} parallel workers")
        log.info(f"📊 Embedding model: {os.getenv('EMBED_MODEL', 'text-embedding-3-large')}, batch size: {batch_size}")

        iterator = tqdm(batches, desc="Embedding batches", unit="batch") if HAS_TQDM else batches

        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = []
            for batch in iterator:
                future = executor.submit(
                    VectorStoreIndex.from_documents,
                    [],
                    storage_context=storage_context,
                    show_progress=False,
                    insert_batch_size=batch_size,
                )
                future.batch_nodes = batch
                futures.append(future)

            for future in futures:
                try:
                    index = future.result()
                    index.insert_nodes(future.batch_nodes)
                except Exception as e:
                    log.error(f"❌ Batch embedding failed: {e}")

        log.info(f"✅ Upsert complete. Processed {len(new_chunks)} chunks.")
    else:
        log.info(f"✅ All {len(all_chunks)} chunks already in Chroma. No embedding needed.")

    # ========== STAGE 3: CONFIRM & UPDATE MANIFEST (Issue 3) ==========
    confirmed_ids = set(chroma_collection.get().get("ids", []))

    for filename, chunk_ids in chunks_per_file.items():
        if chunk_ids and all(cid in confirmed_ids for cid in chunk_ids):
            manifest["files"][filename] = manifest_updates[filename]
            manifest["files"][filename]["embedded"] = True
            log.info(f"✅ Confirmed in ChromaDB: {filename} ({len(chunk_ids)} chunks)")
        else:
            missing = [cid for cid in chunk_ids if cid not in confirmed_ids]
            log.warning(
                f"⚠️  NOT fully embedded: {filename} — "
                f"{len(missing)}/{len(chunk_ids)} chunks missing. Will retry next run."
            )
            # Do NOT update manifest so file is retried on next run

    save_manifest(manifest_file, manifest)

    settings_file.write_text(
        json.dumps(
            {
                "embed_model": os.getenv("EMBED_MODEL", "text-embedding-3-large"),
                "chunk_size": max_chunk_size,
                "chunk_overlap": chunk_overlap,
                "chunking_strategy": "unified (section_wise + llamaparse_v2)",
                "document_types": ["html", "pdf", "docx"],
                "llamaparse": {
                    "tier": lp_cfg.tier,
                    "version": lp_cfg.version,
                    "disable_cache": bool(lp_cfg.disable_cache),
                },
                "last_updated": datetime.utcnow().isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # ========== INTEGRITY REPORT ==========
    print_unified_integrity_report(manifest, chroma_collection, docs_dir)

    # ========== SUMMARY ==========
    log.info("\n" + "=" * 80)
    log.info("✅ INDEXING COMPLETE")
    log.info("=" * 80)
    log.info(f"📦 Chroma DB:        {chroma_dir}")
    log.info(f"🧾 Chunks JSONL:     {chunks_file}")
    log.info(f"⚙️  Settings:         {settings_file}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unified HTML + PDF + DOCX indexer (LlamaParse v2)")
    p.add_argument("--client_root", required=True, help="Path to client root (contains documents/, index_store/)")
    p.add_argument("--rules", type=str, default=None, help="Path to VIQ rules YAML file")
    p.add_argument("--force_reindex", action="store_true", default=False, help="Reprocess files even if hashes match manifest")
    p.add_argument("--max_chunk_size", type=int, default=1200, help="Approx max chars per chunk")
    p.add_argument("--chunk_overlap", type=int, default=150, help="Approx overlap chars between chunks")
    p.add_argument("--parallel_workers", type=int, default=4, help="Parallel workers for embedding upsert")
    p.add_argument("--llamaparse_tier", default=os.getenv("LLAMAPARSE_TIER", "agentic"), help="fast|cost_effective|agentic|agentic_plus")
    p.add_argument("--llamaparse_version", default=os.getenv("LLAMAPARSE_VERSION", "latest"), help="latest or fixed version like 2026-01-08")
    p.add_argument("--llamaparse_disable_cache", action="store_true", default=False, help="Force fresh parsing on LlamaCloud side (disable cache)")
    p.add_argument("--audit", action="store_true", default=False, help="Run integrity audit only — no indexing")
    return p


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    # Load .env from script directory or parent
    try:
        from dotenv import load_dotenv
        script_dir = Path(__file__).resolve().parent
        for candidate in [script_dir / ".env", script_dir.parent / ".env"]:
            if candidate.exists():
                load_dotenv(candidate)
                logging.info(f"Loaded env from {candidate}")
                break
    except ImportError:
        pass

    args = build_arg_parser().parse_args()
    client_root = Path(args.client_root)

    # Issue 4: --audit mode
    if args.audit:
        store_dir = client_root / "index_store"
        chroma_dir = store_dir / "chroma"
        manifest_file = store_dir / "manifest.json"
        manifest = load_manifest(manifest_file)
        db = chromadb.PersistentClient(path=str(chroma_dir))
        chroma_collection = db.get_or_create_collection("docs")
        print_unified_integrity_report(manifest, chroma_collection, client_root / "documents")
        return

    api_key = os.getenv("LLAMA_CLOUD_API_KEY", "").strip()
    rules_path = Path(args.rules) if args.rules else None

    index_client(
        client_root=client_root,
        rules_path=rules_path,
        force_reindex=bool(args.force_reindex),
        max_chunk_size=int(args.max_chunk_size),
        chunk_overlap=int(args.chunk_overlap),
        parallel_workers=int(args.parallel_workers),
        llamaparse_api_key=api_key,
        llamaparse_tier=str(args.llamaparse_tier),
        llamaparse_version=str(args.llamaparse_version),
        llamaparse_disable_cache=bool(args.llamaparse_disable_cache),
    )


if __name__ == "__main__":
    main()
