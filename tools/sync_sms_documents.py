#!/usr/bin/env python3
"""
Daily SMS document sync automation.

Safely pulls the source SMS documents repository, copies only new/changed HTML
files for supported clients, and runs the existing section-wise indexer only for
clients that changed.

Secrets are intentionally read from environment variables. Do not hardcode Git
tokens in this file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, urlunparse


DEFAULT_CLIENTS = ("rsms", "oceangold", "supereco", "primenova", "prime")
DEFAULT_BRANCH = "SMS-Documents-ERP"
DEFAULT_PROJECT_PATH = "SMS-REPO/live-sms-documents.git"

log = logging.getLogger("sms_document_sync")
_INDEXING_IMPORTS_READY = False


@dataclass
class ClientResult:
    client: str
    checked: bool = False
    changed_files: list[str] | None = None
    copied_files: list[str] | None = None
    indexed: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self.changed_files = self.changed_files or []
        self.copied_files = self.copied_files or []


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_clients(value: str | None) -> list[str]:
    raw = value or ",".join(DEFAULT_CLIENTS)
    clients = [c.strip().lower() for c in raw.split(",") if c.strip()]
    seen: set[str] = set()
    ordered = []
    for client in clients:
        if client not in seen:
            seen.add(client)
            ordered.append(client)
    return ordered


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def same_file_content(src: Path, dst: Path) -> bool:
    if not dst.exists() or not dst.is_file():
        return False
    if src.stat().st_size != dst.stat().st_size:
        return False
    return sha256_file(src) == sha256_file(dst)


def ensure_indexing_imports() -> None:
    """Lazy-load RAG/indexing dependencies only when indexing is actually needed."""
    global _INDEXING_IMPORTS_READY
    if _INDEXING_IMPORTS_READY:
        return

    global chromadb, Settings, StorageContext, VectorStoreIndex
    global TextNode, OpenAIEmbedding, ChromaVectorStore
    global _re, _strip_tags, build_breadcrumb, chunk_sections, enrich_chunks
    global extract_toc_sections, file_hash, load_manifest, load_synonyms
    global load_viq_rules, save_manifest, stable_node_id, stable_slug

    import chromadb
    from llama_index.core import Settings, StorageContext, VectorStoreIndex
    from llama_index.core.schema import TextNode
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.vector_stores.chroma import ChromaVectorStore

    from indexer_section_wise import (
        _re,
        _strip_tags,
        build_breadcrumb,
        chunk_sections,
        enrich_chunks,
        extract_toc_sections,
        file_hash,
        load_manifest,
        load_synonyms,
        load_viq_rules,
        save_manifest,
        stable_node_id,
        stable_slug,
    )

    _INDEXING_IMPORTS_READY = True


def mask_secret(text: str) -> str:
    token = os.getenv("SMS_DOCS_GITLAB_TOKEN", "")
    if token:
        text = text.replace(token, "***")
    text = re_mask_basic_auth(text)
    parsed = urlparse(text)
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))
    return text


def re_mask_basic_auth(text: str) -> str:
    marker = "http.extraHeader=Authorization: Basic "
    if marker not in text:
        return text
    before, _, after = text.partition(marker)
    tail = after.split(" ", 1)
    suffix = f" {tail[1]}" if len(tail) == 2 else ""
    return f"{before}{marker}***{suffix}"


def run_cmd(cmd: list[str], cwd: Path | None = None) -> str:
    safe_cmd = " ".join(mask_secret(part) for part in cmd)
    log.debug("Running command: %s", safe_cmd)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {safe_cmd}\n"
            f"stdout: {mask_secret(proc.stdout.strip())}\n"
            f"stderr: {mask_secret(proc.stderr.strip())}"
        )
    output = proc.stdout.strip()
    if output:
        log.debug("Command output: %s", mask_secret(output))
    return output


def build_repo_url() -> str | None:
    """
    Resolve source repository URL from environment.

    Preferred:
      SMS_DOCS_REPO_URL=https://gitlab.com/group/project.git
      SMS_DOCS_GITLAB_TOKEN=<token>

    If SMS_DOCS_REPO_URL already contains credentials, it still works, but that
    is less safe than keeping the token separate.
    """
    repo_url = os.getenv("SMS_DOCS_REPO_URL")
    if not repo_url:
        host = os.getenv("SMS_DOCS_GITLAB_HOST", "gitlab.com")
        project_path = os.getenv("SMS_DOCS_GITLAB_PROJECT", DEFAULT_PROJECT_PATH)
        repo_url = f"https://{host.rstrip('/')}/{project_path.lstrip('/')}"

    return repo_url


def git_auth_args(repo_url: str | None) -> list[str]:
    """
    Add temporary Git HTTPS auth when SMS_DOCS_GITLAB_TOKEN is provided.

    This avoids writing the token into the cloned repo's remote URL. If the URL
    already contains credentials, use it as-is for backward compatibility.
    """
    token = os.getenv("SMS_DOCS_GITLAB_TOKEN")
    if not token or not repo_url:
        return []

    parsed = urlparse(repo_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return []

    username = os.getenv("SMS_DOCS_GITLAB_USERNAME", "oauth2")
    raw = f"{username}:{token}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return ["-c", f"http.extraHeader=Authorization: Basic {encoded}"]


def ensure_repo(repo_dir: Path, branch: str, dry_run: bool, skip_pull: bool) -> bool:
    repo_dir = repo_dir.resolve()
    repo_url = build_repo_url()
    auth_args = git_auth_args(repo_url)

    if (repo_dir / ".git").exists():
        log.info("Source repo exists: %s", repo_dir)
        if skip_pull:
            log.info("Skipping pull because --skip-pull was set")
            return True
        if dry_run:
            log.info("[DRY RUN] Would pull latest changes for branch %s", branch)
            return True
        run_cmd(["git", *auth_args, "fetch", "origin", branch], cwd=repo_dir)
        run_cmd(["git", "checkout", branch], cwd=repo_dir)
        output = run_cmd(["git", *auth_args, "pull", "--ff-only", "origin", branch], cwd=repo_dir)
        log.info("Pulled source repo%s", f": {mask_secret(output)}" if output else "")
        return True

    if not repo_url:
        raise RuntimeError(
            "Source repo is missing and no repository URL is configured. "
            "Set SMS_DOCS_REPO_URL or SMS_DOCS_GITLAB_TOKEN/project env vars."
        )

    if dry_run:
        log.info("[DRY RUN] Would clone %s into %s", mask_secret(repo_url), repo_dir)
        return False

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    log.info("Cloning source repo into %s", repo_dir)
    run_cmd(
        [
            "git",
            *auth_args,
            "clone",
            "--single-branch",
            "--branch",
            branch,
            repo_url,
            str(repo_dir),
        ],
        cwd=repo_dir.parent,
    )
    return True


def html_files(source_docs_dir: Path) -> list[Path]:
    files = list(source_docs_dir.glob("*.html")) + list(source_docs_dir.glob("*.htm"))
    return sorted(p for p in files if p.is_file())


def detect_destination_only_files(source_docs_dir: Path, target_docs_dir: Path) -> list[str]:
    """Return HTML files that exist only in destination. They are logged, never deleted."""
    if not target_docs_dir.exists():
        return []
    source_names = {p.name for p in html_files(source_docs_dir)}
    target_names = {p.name for p in html_files(target_docs_dir)}
    return sorted(target_names - source_names)


def backup_existing_file(dst: Path, backup_root: Path) -> Path | None:
    if not dst.exists():
        return None
    relative_name = dst.name
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / relative_name
    if backup_path.exists():
        backup_path = backup_root / f"{dst.stem}.{utc_stamp()}{dst.suffix}"
    shutil.copy2(dst, backup_path)
    return backup_path


def copy_changed_files(
    client: str,
    source_docs_dir: Path,
    target_docs_dir: Path,
    backup_root: Path,
    dry_run: bool,
) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    copied: list[str] = []

    log.info("[%s] Checking source folder: %s", client, source_docs_dir)
    if not source_docs_dir.exists():
        raise FileNotFoundError(f"Source documents folder not found: {source_docs_dir}")

    source_files = html_files(source_docs_dir)
    log.info("[%s] Found %s HTML files in source", client, len(source_files))

    target_docs_dir.mkdir(parents=True, exist_ok=True)
    destination_only = detect_destination_only_files(source_docs_dir, target_docs_dir)
    if destination_only:
        sample = ", ".join(destination_only[:25])
        suffix = f" ... (+{len(destination_only) - 25} more)" if len(destination_only) > 25 else ""
        log.info(
            "[%s] Destination-only HTML files retained, not deleted (%s): %s%s",
            client,
            len(destination_only),
            sample,
            suffix,
        )
    client_backup_root = backup_root / client / utc_stamp()

    for src in source_files:
        dst = target_docs_dir / src.name
        if same_file_content(src, dst):
            continue

        changed.append(src.name)
        if dry_run:
            log.info("[%s] [DRY RUN] Would copy: %s", client, src.name)
            continue

        backup_path = backup_existing_file(dst, client_backup_root)
        if backup_path:
            log.info("[%s] Backed up existing file: %s", client, backup_path)

        tmp_dst = dst.with_suffix(dst.suffix + ".tmp")
        shutil.copy2(src, tmp_dst)
        os.replace(tmp_dst, dst)
        copied.append(src.name)
        log.info("[%s] Copied: %s", client, src.name)

    if changed:
        log.info("[%s] Changed files: %s", client, ", ".join(changed))
    else:
        log.info("[%s] No new/changed HTML files", client)

    return changed, copied


def create_stub_node(file_path: Path, html_text: str) -> TextNode:
    doc_title = file_path.stem.replace("_", " ")
    heading_match = _re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html_text, _re.I | _re.S)
    if heading_match:
        heading_title = _strip_tags(heading_match.group(1)).strip()
        if heading_title and len(heading_title) > 2:
            doc_title = heading_title

    stub_id = stable_slug(doc_title)
    node = TextNode(
        text=doc_title,
        metadata={
            "file": file_path.name,
            "section_id": stub_id,
            "section_title": doc_title,
            "breadcrumb": build_breadcrumb(file_path.stem, doc_title),
            "slug_url": f"{file_path.name}#{stub_id}",
            "chunk_index": 0,
            "is_complete_section": True,
            "stub": True,
        },
    )
    node.id_ = stable_node_id(file_path.name, stub_id, 0)
    return node


def load_chunks_jsonl(chunks_file: Path) -> list[dict]:
    if not chunks_file.exists():
        return []
    rows: list[dict] = []
    with chunks_file.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Skipping invalid JSONL row %s in %s", line_no, chunks_file)
    return rows


def rewrite_chunks_jsonl(
    chunks_file: Path,
    changed_names: set[str],
    new_chunks: list[TextNode],
    backup_dir: Path,
) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if chunks_file.exists():
        backup_path = backup_dir / f"{chunks_file.name}.{utc_stamp()}.bak"
        shutil.copy2(chunks_file, backup_path)
        log.info("Backed up chunks JSONL: %s", backup_path)

    kept_rows = []
    for row in load_chunks_jsonl(chunks_file):
        filename = (row.get("metadata") or {}).get("file")
        if filename not in changed_names:
            kept_rows.append(row)

    new_rows = [{"id_": chunk.id_, "text": chunk.text, "metadata": chunk.metadata} for chunk in new_chunks]
    tmp_file = chunks_file.with_suffix(chunks_file.suffix + ".tmp")
    with tmp_file.open("w", encoding="utf-8") as fh:
        for row in kept_rows + new_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_file, chunks_file)
    log.info("Updated chunks JSONL: kept=%s new=%s file=%s", len(kept_rows), len(new_rows), chunks_file)


def json_safe(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def backup_chroma_records(collection, filename: str, backup_dir: Path) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    records = collection.get(where={"file": filename}, include=["documents", "metadatas", "embeddings"])
    if records.get("ids"):
        backup_file = backup_dir / f"chroma-{filename}.{utc_stamp()}.json"
        embeddings = records.get("embeddings")
        serializable = {
            "ids": records.get("ids") or [],
            "documents": records.get("documents") or [],
            "metadatas": records.get("metadatas") or [],
            "embeddings": json_safe(embeddings if embeddings is not None else []),
        }
        backup_file.write_text(json.dumps(serializable), encoding="utf-8")
        log.info("Backed up Chroma records for %s: %s", filename, backup_file)
    return records


def restore_chroma_records(collection, records: dict) -> None:
    ids = records.get("ids") or []
    if not ids:
        return
    collection.add(
        ids=ids,
        documents=records.get("documents") or None,
        metadatas=records.get("metadatas") or None,
        embeddings=records.get("embeddings") or None,
    )


def delete_chroma_records(collection, filename: str) -> int:
    existing = collection.get(where={"file": filename}, include=[])
    ids = existing.get("ids") or []
    if ids:
        collection.delete(ids=ids)
        log.info("Deleted %s old Chroma chunks for %s", len(ids), filename)
    return len(ids)


def insert_file_chunks(collection, storage_context, filename: str, chunks: list[TextNode], backup_dir: Path) -> None:
    old_records = backup_chroma_records(collection, filename, backup_dir)
    delete_chroma_records(collection, filename)
    try:
        index = VectorStoreIndex.from_documents(
            [],
            storage_context=storage_context,
            show_progress=False,
            insert_batch_size=256,
        )
        index.insert_nodes(chunks)
    except Exception:
        log.exception("Embedding/index insertion failed for %s; restoring old Chroma records", filename)
        restore_chroma_records(collection, old_records)
        raise

    confirmed = set(collection.get(where={"file": filename}, include=[]).get("ids") or [])
    missing = [chunk.id_ for chunk in chunks if chunk.id_ not in confirmed]
    if missing:
        raise RuntimeError(f"Chroma confirmation failed for {filename}; missing {len(missing)} chunks")
    log.info("Confirmed %s Chroma chunks for %s", len(chunks), filename)


def build_file_chunks(file_path: Path, max_chunk_size: int, chunk_overlap: int) -> tuple[list[TextNode], dict]:
    html_text = file_path.read_text(encoding="utf-8", errors="replace")
    sections = extract_toc_sections(html_text, file_path.name)
    chunks = chunk_sections(
        sections=sections,
        filename=file_path.name,
        max_chunk_size=max_chunk_size,
        min_chunk_size=100,
        overlap=chunk_overlap,
    )

    is_stub = False
    if not chunks:
        chunks = [create_stub_node(file_path, html_text)]
        is_stub = True
        log.warning("Image-only file; created title stub: %s", file_path.name)

    manifest_entry = {
        "sha256": file_hash(file_path),
        "mtime": file_path.stat().st_mtime,
        "sections": len(sections),
        "chunks": len(chunks),
        "stub": is_stub,
    }
    return chunks, manifest_entry


def write_index_settings(settings_file: Path, max_chunk_size: int, chunk_overlap: int) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "embed_model": os.getenv("EMBED_MODEL", "text-embedding-3-large"),
                "chunk_size": max_chunk_size,
                "chunk_overlap": chunk_overlap,
                "chunking_strategy": "section_wise_v2_daily_sync_incremental",
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_incremental_index(
    client: str,
    client_root: Path,
    changed_filenames: Iterable[str],
    rules_path: Path,
    dry_run: bool,
    max_chunk_size: int = 1500,
    chunk_overlap: int = 100,
) -> None:
    filenames = sorted(set(changed_filenames))
    if dry_run:
        log.info("[%s] [DRY RUN] Would incrementally index files: %s", client, ", ".join(filenames))
        return
    if not filenames:
        return
    ensure_indexing_imports()

    docs_dir = client_root / "documents"
    store_dir = client_root / "index_store"
    chroma_dir = store_dir / "chroma"
    chunks_file = store_dir / "chunks.jsonl"
    manifest_file = store_dir / "manifest.json"
    settings_file = store_dir / "settings.json"
    backup_dir = store_dir / "auto_index_backups" / utc_stamp()

    store_dir.mkdir(parents=True, exist_ok=True)
    log.info("[%s] Starting safe incremental indexing for %s file(s)", client, len(filenames))

    viq_rules = load_viq_rules(rules_path)
    synonyms = load_synonyms(rules_path)
    manifest = load_manifest(manifest_file)

    Settings.embed_model = OpenAIEmbedding(
        model=os.getenv("EMBED_MODEL", "text-embedding-3-large"),
        embed_batch_size=int(os.getenv("EMBED_BATCH", "256")),
        timeout=60,
    )
    db = chromadb.PersistentClient(path=str(chroma_dir))
    collection = db.get_or_create_collection("docs")
    vector_store = ChromaVectorStore(chroma_collection=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    all_new_chunks: list[TextNode] = []
    manifest_updates: dict[str, dict] = {}
    embedded_files: set[str] = set()

    for filename in filenames:
        file_path = docs_dir / filename
        if not file_path.exists():
            log.warning("[%s] Changed file missing after copy, skipping: %s", client, file_path)
            continue

        chunks, manifest_entry = build_file_chunks(file_path, max_chunk_size, chunk_overlap)
        if viq_rules or synonyms:
            chunks = enrich_chunks(chunks, viq_rules, synonyms)

        insert_file_chunks(collection, storage_context, filename, chunks, backup_dir)
        all_new_chunks.extend(chunks)
        manifest_updates[filename] = manifest_entry
        manifest_updates[filename]["embedded"] = True
        embedded_files.add(filename)

    if all_new_chunks:
        rewrite_chunks_jsonl(chunks_file, embedded_files, all_new_chunks, backup_dir)
        for filename in embedded_files:
            manifest["files"][filename] = manifest_updates[filename]
        save_manifest(manifest_file, manifest)
        write_index_settings(settings_file, max_chunk_size, chunk_overlap)
        log.info("[%s] Updated manifest only after Chroma confirmation: %s", client, manifest_file)

    log.info("[%s] Safe incremental indexing completed", client)


@contextmanager
def lock_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("w", encoding="utf-8")
    try:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another document sync is already running: {path}") from exc
        fh.write(f"pid={os.getpid()} started_at={datetime.now(timezone.utc).isoformat()}\n")
        fh.flush()
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def setup_logging(log_file: Path, verbose: bool) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )


def process_clients(args: argparse.Namespace) -> list[ClientResult]:
    clients = parse_clients(args.clients)
    results: list[ClientResult] = []

    repo_available = ensure_repo(
        repo_dir=args.repo_dir,
        branch=args.branch,
        dry_run=args.dry_run,
        skip_pull=args.skip_pull,
    )
    if not repo_available:
        log.info("Source repo is not available locally; stopping after dry-run clone preview")
        return results

    for client in clients:
        result = ClientResult(client=client)
        results.append(result)
        try:
            source_docs_dir = args.repo_dir / client / "documents"
            client_root = args.data_dir / client
            target_docs_dir = client_root / "documents"

            changed, copied = copy_changed_files(
                client=client,
                source_docs_dir=source_docs_dir,
                target_docs_dir=target_docs_dir,
                backup_root=args.backup_dir,
                dry_run=args.dry_run,
            )
            result.checked = True
            result.changed_files = changed
            result.copied_files = copied

            should_index = bool(changed if args.dry_run else copied)
            if should_index:
                run_incremental_index(
                    client=client,
                    client_root=client_root,
                    changed_filenames=changed if args.dry_run else copied,
                    rules_path=args.rules,
                    dry_run=args.dry_run,
                )
                result.indexed = not args.dry_run
            else:
                log.info("[%s] Skipping indexing because nothing changed", client)

        except Exception as exc:
            result.error = str(exc)
            log.exception("[%s] Failed; continuing with next client", client)

    return results


def build_parser() -> argparse.ArgumentParser:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Sync SMS HTML documents and index changed clients.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path(os.getenv("SMS_DOCS_REPO_DIR", root / ".cache" / "live-sms-documents")),
        help="Local clone path for the source documents repo.",
    )
    parser.add_argument(
        "--branch",
        default=os.getenv("SMS_DOCS_REPO_BRANCH", DEFAULT_BRANCH),
        help="Source repo branch to clone/pull.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("SMS_RAG_DATA_DIR", os.getenv("DATA_ROOT", os.getenv("BASE_DIR", root / "data")))),
        help="SMS RAG data directory containing <client>/documents.",
    )
    parser.add_argument(
        "--clients",
        default=os.getenv("SMS_RAG_CLIENTS", ",".join(DEFAULT_CLIENTS)),
        help="Comma-separated client folder names to sync.",
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(os.getenv("SMS_RAG_RULES", root / "rules.yaml")),
        help="Path to rules.yaml for the existing indexer.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path(os.getenv("SMS_DOCS_BACKUP_DIR")) if os.getenv("SMS_DOCS_BACKUP_DIR") else None,
        help="Where overwritten destination documents are backed up.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path(os.getenv("SMS_DOCS_SYNC_LOG")) if os.getenv("SMS_DOCS_SYNC_LOG") else None,
        help="Log file path.",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(os.getenv("SMS_DOCS_SYNC_LOCK")) if os.getenv("SMS_DOCS_SYNC_LOCK") else None,
        help="Prevents overlapping sync runs.",
    )
    parser.add_argument("--skip-pull", action="store_true", help="Use existing repo-dir without git pull.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without copying/indexing.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.data_dir = args.data_dir.expanduser().resolve()
    if args.backup_dir is None:
        args.backup_dir = args.data_dir / "_document_sync_backups"
    if args.log_file is None:
        args.log_file = args.data_dir / "_document_sync_logs" / "sms_document_sync.log"
    if args.lock_file is None:
        args.lock_file = args.data_dir / "_document_sync_logs" / "sms_document_sync.lock"

    setup_logging(args.log_file, args.verbose)
    log.info("=" * 72)
    log.info("Starting SMS document sync")
    log.info("Repo dir: %s", args.repo_dir)
    log.info("Branch: %s", args.branch)
    log.info("Data dir: %s", args.data_dir)
    log.info("Clients: %s", ", ".join(parse_clients(args.clients)))
    log.info("Dry run: %s", args.dry_run)

    try:
        with lock_file(args.lock_file):
            results = process_clients(args)
    except Exception:
        log.exception("SMS document sync failed before client processing")
        return 1

    failures = [r for r in results if r.error]
    indexed = [r.client for r in results if r.indexed]
    changed = [r.client for r in results if r.changed_files]

    log.info("=" * 72)
    log.info("SMS document sync summary")
    log.info("Changed clients: %s", ", ".join(changed) if changed else "none")
    log.info("Indexed clients: %s", ", ".join(indexed) if indexed else "none")
    if failures:
        for failure in failures:
            log.error("[%s] Error: %s", failure.client, failure.error)
    log.info("Completed SMS document sync with %s failure(s)", len(failures))

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
