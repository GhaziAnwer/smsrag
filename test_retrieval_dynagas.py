#!/usr/bin/env python
"""
Standalone test harness for the dynagas retrieval pipeline.
Imports and reuses actual production functions — does NOT rewrite them.

Usage:
    python test_retrieval_dynagas.py "emergency procedures"
"""
import sys, os, re

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── 1. Environment setup ─────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(r"D:\indexstores\.env")          # OPENAI_API_KEY
load_dotenv(r"C:\Users\GhaziAnwer\rag\dev\.env", override=True)  # fallback

os.environ["BASE_DIR"] = r"D:\indexstores"
os.environ["INITIAL_RETRIEVE_K"] = "25"
os.environ["FINAL_SYNTHESIS_K"] = "8"
os.environ["LOG_CONFIG_ON_IMPORT"] = "false"  # suppress startup banner

# Add production code to path so we can import app.*
PROD_ROOT = r"C:\Users\GhaziAnwer\rag\prodcution"
if PROD_ROOT not in sys.path:
    sys.path.insert(0, PROD_ROOT)

# ── 2. Import production modules ─────────────────────────────────────────────
from app.services import build_retriever_bundle
from app.rerankers.reranker_llm import OpenAILLMReranker, LLMRerankerConfig
from app.models import RefItem

# Import reference-building helpers from query.py
from app.routers.query import (
    _build_references,
    _refs_html,
    _apply_reranking,
    _doc_url,
    _clean_title,
    _clean_breadcrumb,
    MIN_REF_SCORE,
)

CLIENT_ID = "dynagas"
CHROMA_PATH = r"D:\indexstores\dynagas\index_store\chroma"
CHUNKS_PATH = r"D:\indexstores\dynagas\index_store\chunks.jsonl"


def build_bundle():
    """Build retriever bundle pointing at dynagas chroma."""
    paths = {
        "chroma_path": CHROMA_PATH,
        "chunks_path": CHUNKS_PATH,
        "settings_path": r"D:\indexstores\dynagas\index_store\settings.json",
        "rules_yaml": r"D:\indexstores\dynagas\rules.yaml",
    }
    return build_retriever_bundle(paths=paths, tenant=CLIENT_ID, index=CLIENT_ID)


def run_test(query: str):
    """Run full retrieval → rerank → references pipeline for one query."""
    print(f"\n{'='*80}")
    print(f"=== QUERY: {query} ===")
    print(f"{'='*80}")

    # ── Build retriever ───────────────────────────────────────────────────
    bundle = build_bundle()
    retriever = bundle["retriever"]

    # ── Retrieve top 25 ──────────────────────────────────────────────────
    initial_k = int(os.getenv("INITIAL_RETRIEVE_K", "25"))
    retriever._similarity_top_k = initial_k
    nodes = retriever.retrieve(query)

    print(f"\n--- TOP {len(nodes)} RETRIEVED (pre-rerank) ---")
    missing_fields_found = []
    required_fields = ["file", "slug_url", "breadcrumb", "section_title"]

    for rank, n in enumerate(nodes, 1):
        md = getattr(n.node, "metadata", {}) or {}
        score = getattr(n, "score", None)
        text = getattr(n.node, "text", "")
        file_val = md.get("file", "")
        source_type = md.get("source_type", "?")
        breadcrumb = md.get("breadcrumb", "")
        slug_url = md.get("slug_url", "")
        section_title = md.get("section_title", "")

        print(f"Rank {rank:2d} | Score: {score:.4f} | File: {file_val[:60]} | Type: {source_type}")
        print(f"  Breadcrumb : {breadcrumb[:80]}")
        print(f"  SlugURL    : {slug_url[:80]}")
        print(f"  Text       : {text[:200].replace(chr(10), ' ')}...")

        # Check for missing/empty fields
        empty = [f for f in required_fields if not md.get(f)]
        if empty:
            print(f"  ⚠️  MISSING FIELDS: {empty}")
            missing_fields_found.append((rank, file_val, empty))
        print()

    # ── Rerank (uses actual production _apply_reranking) ─────────────────
    print(f"\n--- TOP 8 AFTER RERANKING ---")
    reranked = _apply_reranking(query, nodes, use_reranker=True)

    final_k = int(os.getenv("FINAL_SYNTHESIS_K", "8"))
    final_nodes = reranked[:final_k]

    for rank, n in enumerate(final_nodes, 1):
        md = getattr(n.node, "metadata", {}) or {}
        score = getattr(n, "score", None)
        file_val = md.get("file", "")
        breadcrumb = md.get("breadcrumb", "")
        score_str = f"{score:.4f}" if score is not None else "N/A"
        print(f"Rank {rank:2d} | Score: {score_str} | File: {file_val[:60]}")
        print(f"  Breadcrumb : {breadcrumb[:80]}")

    # ── Build references (as user sees them in UI) ───────────────────────
    print(f"\n--- REFERENCES (as user sees them in UI) ---")
    refs = _build_references(final_nodes, CLIENT_ID)
    refs_block = _refs_html(refs)

    if refs:
        for i, r in enumerate(refs, 1):
            print(f"  Ref {i}: title={r.title!r}")
            print(f"          breadcrumb={r.breadcrumb!r}")
            print(f"          url={r.url!r}")
            print(f"          score={r.score}")
    else:
        print("  (no references produced)")

    print(f"\n--- RAW HTML ---")
    print(refs_block[:1000] if refs_block else "(empty)")

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n--- DIAGNOSTICS ---")
    print(f"Total retrieved: {len(nodes)}")
    print(f"After reranking (top {final_k}): {len(final_nodes)}")
    print(f"References produced: {len(refs)}")
    if missing_fields_found:
        print(f"⚠️  Chunks with missing fields: {len(missing_fields_found)}")
        for rank, fname, fields in missing_fields_found:
            print(f"   Rank {rank}: {fname[:50]} missing {fields}")
    else:
        print("✅ All chunks have all 4 required metadata fields")

    # Check slug_url format
    bad_slugs = []
    for n in nodes:
        md = getattr(n.node, "metadata", {}) or {}
        slug = md.get("slug_url", "")
        f = md.get("file", "")
        if slug and "#" not in slug and not slug.endswith((".pdf", ".docx", ".doc")):
            bad_slugs.append((f, slug))
    if bad_slugs:
        print(f"⚠️  Chunks with malformed slug_url (no anchor): {len(bad_slugs)}")
        for f, s in bad_slugs[:3]:
            print(f"   {f}: {s}")
    else:
        print("✅ All slug_urls have correct format (filename#anchor)")

    # Check breadcrumb quality
    bad_bc = []
    for n in nodes:
        md = getattr(n.node, "metadata", {}) or {}
        bc = md.get("breadcrumb", "")
        if "_Toc" in bc or re.search(r'Section \d{5,}', bc):
            bad_bc.append(bc)
    if bad_bc:
        print(f"⚠️  Breadcrumbs with _Toc codes or machine IDs: {len(bad_bc)}")
        for b in bad_bc[:3]:
            print(f"   {b}")
    else:
        print("✅ All breadcrumbs are clean (no _Toc codes)")

    return refs


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_retrieval_dynagas.py \"your query here\"")
        sys.exit(1)

    query = sys.argv[1]
    run_test(query)
