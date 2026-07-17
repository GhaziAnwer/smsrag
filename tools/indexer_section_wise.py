#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indexer_section_wise.py — SECTION-WISE CHUNKING INDEXER (v2 - Fixed)

Fixes:
  - Handles both name="_Toc..." and id="_Toc..." anchor formats
  - Extracts titles from span elements within anchor tags
  - Better title extraction for Word-to-HTML exports
  
Algorithm:
  1. Find all _Toc anchors (name= or id= attribute)
  2. Extract text between consecutive anchors
  3. Each section = one chunk (or split if too large)
  4. Direct mapping: chunk → _Toc ID
"""

import os, re, json, argparse, hashlib, logging, asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import concurrent.futures

import html as _html
import re as _re

# LlamaIndex core
from llama_index.core import Document, StorageContext, VectorStoreIndex, Settings
from llama_index.core.schema import TextNode
from llama_index.core.node_parser.text import SentenceSplitter

# Embeddings + Vector store
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# Vector DB
import chromadb
import yaml

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False
    logging.warning("tqdm not installed. Install with 'pip install tqdm' for progress bars.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ============================= UTILITY FUNCTIONS =============================

def _strip_tags(s: str) -> str:
    """Remove HTML tags and decode entities."""
    if not s:
        return ""
    s = _re.sub(r'<\s*script[^>]*>.*?</\s*script\s*>', " ", s, flags=_re.I|_re.S)
    s = _re.sub(r'<\s*style[^>]*>.*?</\s*style\s*>', " ", s, flags=_re.I|_re.S)
    s = _re.sub(r'<[^>]+>', " ", s)
    s = _html.unescape(s)
    return _re.sub(r'\s+', ' ', s).strip()

def _norm_txt(s: str) -> str:
    """Normalize text for matching."""
    text = (s or "").strip()
    text = _re.sub(r'^\s*[\d\.]+\s*', '', text)  # Remove leading numbers
    text = text.lower()
    text = _re.sub(r'\s+', ' ', text).strip()
    return text

def stable_slug(s: str) -> str:
    """Generate stable slug for fallback IDs."""
    s = (s or "untitled").strip().lower()
    s = _re.sub(r'[^\w\s-]', '', s)
    s = _re.sub(r'[-\s]+', '-', s)
    return f"sec-{s[:50]}" if s else "sec-untitled"

def build_breadcrumb(doc: str, section: str) -> str:
    """Build breadcrumb path."""
    doc = doc.replace(".html", "")
    if not section or section == doc:
        return doc
    return f"{doc} > {section}"

def file_hash(path: Path) -> str:
    """Calculate SHA256 hash of file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Load indexing manifest."""
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except:
            pass
    return {"timestamp": None, "files": {}}

def save_manifest(manifest_path: Path, manifest: Dict[str, Any]):
    """Save indexing manifest."""
    manifest["timestamp"] = datetime.utcnow().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

def stable_node_id(file: str, section_id: str, chunk_index: int = 0) -> str:
    """Generate stable node ID."""
    s = f"{file}::{section_id}::{chunk_index}"
    return f"node-{hashlib.sha1(s.encode()).hexdigest()}"

# ============================= IMPROVED TITLE EXTRACTION =============================

def extract_title_from_anchor_content(anchor_html: str) -> str:
    """
    Extract title from anchor tag content, handling span-wrapped text.
    
    Handles patterns like:
    <a href="#_Toc...">
      <span>1.</span>
      <span>&#xa0;</span>  <!-- non-breaking space -->
      <span>KEY RESPONSIBILITIES</span>
    </a>
    """
    # First try: extract all text and clean it
    full_text = _strip_tags(anchor_html).strip()
    
    # Remove leading numbers like "1.", "4.2.1", etc.
    clean_text = _re.sub(r'^[\d\.]+\s*', '', full_text).strip()
    
    if clean_text and len(clean_text) > 2:
        return clean_text
    
    # If that didn't work, try extracting from spans
    spans = _re.findall(r'<span[^>]*>(.*?)</span>', anchor_html, _re.I|_re.S)
    
    # Filter out number-only spans and whitespace spans
    text_spans = []
    for span in spans:
        text = _strip_tags(span).strip()
        # Skip if empty, just whitespace, or just numbers/dots
        if text and not _re.match(r'^[\d\.\s]*$', text) and text not in ['', ' ', '\xa0']:
            text_spans.append(text)
    
    if text_spans:
        return ' '.join(text_spans)
    
    return full_text if full_text else ""


def extract_better_title(html_text: str, anchor_end_pos: int, fallback_id: str) -> str:
    """
    Extract title from nearby heading tags or text after anchor.
    
    This fixes the issue where Word-to-HTML exports have empty <a> tags
    with the actual title in a following element.
    
    Handles Aspose format: <a name="_Toc..."></a><a name="..."><span>Title</span></a>
    """
    # Look 1000 chars after the anchor (titles should be close)
    window = html_text[anchor_end_pos:anchor_end_pos + 1000]
    
    # Strategy 0 (NEW): Aspose pattern - next <a> tag with <span> containing title
    # Pattern: <a name="..."><span>TITLE</span></a>
    aspose_match = _re.search(
        r'<a[^>]*name=["\'][^"\']+["\'][^>]*>\s*(?:<[^>]*>)*\s*<span[^>]*>([^<]+)</span>',
        window, _re.I|_re.S
    )
    if aspose_match:
        title = aspose_match.group(1).strip()
        # Remove leading numbers like "3." or "4.2.1"  
        title = _re.sub(r'^[\d\.]+\s*', '', title).strip()
        if title and len(title) > 2 and len(title) < 200:
            return title
    
    # Strategy 1: Try h1-h6 tags first (most reliable for section titles)
    heading_match = _re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', window, _re.I|_re.S)
    if heading_match:
        title = _strip_tags(heading_match.group(1)).strip()
        if title and len(title) > 2:
            return title
    
    # Strategy 2: Try bold/strong tags
    bold_match = _re.search(r'<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>', window, _re.I|_re.S)
    if bold_match:
        title = _strip_tags(bold_match.group(1)).strip()
        if title and len(title) > 2 and len(title) < 200:
            return title
    
    # Strategy 3: Try <p> with heading-related class
    p_heading_match = _re.search(r'<p[^>]*class="[^"]*(?:heading|title|Heading)[^"]*"[^>]*>(.*?)</p>', window, _re.I|_re.S)
    if p_heading_match:
        title = _strip_tags(p_heading_match.group(1)).strip()
        if title and len(title) > 2 and len(title) < 200:
            return title
    
    # Strategy 4: Try styled span with bold
    span_match = _re.search(r'<span[^>]*style="[^"]*font-weight:\s*(?:bold|700)[^"]*"[^>]*>(.*?)</span>', window, _re.I|_re.S)
    if span_match:
        title = _strip_tags(span_match.group(1)).strip()
        if title and len(title) > 2 and len(title) < 200:
            return title
    
    # Strategy 5: First meaningful text line
    clean_window = _strip_tags(window[:500])
    first_line_match = _re.search(r'^([^\n\r]{3,100})', clean_window.strip())
    if first_line_match:
        title = first_line_match.group(1).strip()
        title = _re.sub(r'^[\d\.]+\s*', '', title).strip()  # Remove leading numbers
        if title and not _re.match(r'^[\d\s\.\-]+$', title):
            return title
    
    return fallback_id

# ============================= SECTION EXTRACTION =============================

class TocSection:
    """Represents a _Toc section with its content."""
    def __init__(self, toc_id: str, title: str, start_pos: int, end_pos: int, html: str):
        self.toc_id = toc_id
        self.title = title
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.html = html[start_pos:end_pos]
        self.text = _strip_tags(self.html)
    
    def __repr__(self):
        return f"TocSection({self.toc_id}, '{self.title[:30]}...', {len(self.text)} chars)"


def extract_toc_sections(html_text: str, filename: str) -> List[TocSection]:
    """
    Extract all _Toc sections from HTML.
    
    Handles multiple anchor formats including Aspose Word-to-HTML exports:
    1. <a name="_Toc...">Title</a>           - Standard Word export
    2. <a id="_Toc...">Title</a>             - Alternative format
    3. <a name="_Toc..."></a><h2>Title</h2>  - Empty anchor with heading after
    4. <a name="_Toc..."></a><a name="_Toc..."><span>Title</span></a>  - Aspose format
    
    Returns list of TocSection objects.
    """
    sections = []
    anchors = []
    
    # ========== Pattern 1: name="_Toc..." or id="_Toc..." attributes ==========
    # Matches: <a name="_TocXXX">...</a> or <a id="_TocXXX">...</a>
    pattern1 = r'<a[^>]*\b(?:name|id)=["\'](_Toc[0-9A-Za-z_:-]+)["\'][^>]*>(.*?)</a>'
    
    for m in _re.finditer(pattern1, html_text, flags=_re.I|_re.S):
        toc_id = m.group(1)
        anchor_content = m.group(2)
        anchor_end_pos = m.end()
        
        # Try to get title from anchor content first
        title = extract_title_from_anchor_content(anchor_content)
        
        # If anchor is empty or just has numbers, look for title after anchor
        if not title or len(title) < 3 or title.startswith('_Toc') or _re.match(r'^[\d\.]+$', title):
            # Aspose format: multiple empty anchors followed by one anchor with content
            # Look at the next 500 chars for an <a> tag that contains actual text
            lookahead = html_text[anchor_end_pos:anchor_end_pos + 500]
            
            # Pattern: <a name="...">...<span>TITLE</span>...</a>
            next_anchor_match = _re.search(
                r'<a[^>]*name=["\'][^"\']+["\'][^>]*>(.*?)</a>',
                lookahead,
                _re.I|_re.S
            )
            if next_anchor_match:
                next_content = next_anchor_match.group(1)
                next_title = extract_title_from_anchor_content(next_content)
                if next_title and len(next_title) > 2 and not next_title.startswith('_Toc'):
                    title = next_title
            
            # If still no title, use the general title extraction
            if not title or len(title) < 3 or title.startswith('_Toc'):
                title = extract_better_title(html_text, anchor_end_pos, toc_id)
        
        anchors.append({
            'id': toc_id,
            'title': title,
            'start': anchor_end_pos,
            'match_start': m.start()
        })
    
    # ========== Pattern 2: Self-closing anchors ==========
    # Matches: <a name="_TocXXX"/> or <a name="_TocXXX" />
    pattern2 = r'<a[^>]*\b(?:name|id)=["\'](_Toc[0-9A-Za-z_:-]+)["\'][^>]*/>'
    
    for m in _re.finditer(pattern2, html_text, flags=_re.I|_re.S):
        toc_id = m.group(1)
        anchor_end_pos = m.end()
        
        # Self-closing anchors have no content, look for title after
        title = extract_better_title(html_text, anchor_end_pos, toc_id)
        
        # Avoid duplicates
        if not any(a['id'] == toc_id for a in anchors):
            anchors.append({
                'id': toc_id,
                'title': title,
                'start': anchor_end_pos,
                'match_start': m.start()
            })
    
    # ========== Pattern 3: Bookmarks as span/div with id ==========
    # Some exports use: <span id="_Toc..."></span> or <div id="_Toc...">
    pattern3 = r'<(?:span|div)[^>]*\bid=["\'](_Toc[0-9A-Za-z_:-]+)["\'][^>]*>(.*?)</(?:span|div)>'
    
    for m in _re.finditer(pattern3, html_text, flags=_re.I|_re.S):
        toc_id = m.group(1)
        content = m.group(2)
        anchor_end_pos = m.end()
        
        title = _strip_tags(content).strip()
        if not title or len(title) < 3:
            title = extract_better_title(html_text, anchor_end_pos, toc_id)
        
        # Avoid duplicates
        if not any(a['id'] == toc_id for a in anchors):
            anchors.append({
                'id': toc_id,
                'title': title,
                'start': anchor_end_pos,
                'match_start': m.start()
            })
    
    # Sort by position in document
    anchors.sort(key=lambda x: x['match_start'])
    
    # Remove duplicates (keep first occurrence)
    seen_ids = set()
    unique_anchors = []
    for anchor in anchors:
        if anchor['id'] not in seen_ids:
            seen_ids.add(anchor['id'])
            unique_anchors.append(anchor)
    anchors = unique_anchors
    
    log.info(f"Found {len(anchors)} _Toc anchors in {filename}")
    
    # Log first few titles for debugging
    for i, anchor in enumerate(anchors[:5]):
        log.debug(f"  Anchor {i+1}: {anchor['id']} -> '{anchor['title'][:50]}...'")
    
    # Create sections between consecutive anchors
    for i, anchor in enumerate(anchors):
        # End position = start of next anchor (or end of document)
        if i + 1 < len(anchors):
            end_pos = anchors[i + 1]['match_start']
        else:
            end_pos = len(html_text)
        
        # Create section
        section = TocSection(
            toc_id=anchor['id'],
            title=anchor['title'],
            start_pos=anchor['start'],
            end_pos=end_pos,
            html=html_text
        )
        
        # Only add if has meaningful content
        if len(section.text.strip()) > 10:
            sections.append(section)
    
    # Fallback: if no _Toc anchors found but file has text, treat entire doc as one section
    if not sections:
        full_text = _strip_tags(html_text)
        if len(full_text.strip()) > 50:
            doc_title = Path(filename).stem.replace("_", " ")
            # Try to extract a better title from the first heading
            heading_match = _re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html_text, _re.I|_re.S)
            if heading_match:
                ht = _strip_tags(heading_match.group(1)).strip()
                if ht and len(ht) > 2:
                    doc_title = ht
            fallback_section = TocSection(
                toc_id=stable_slug(doc_title),
                title=doc_title,
                start_pos=0,
                end_pos=len(html_text),
                html=html_text
            )
            sections.append(fallback_section)
            log.info(f"⚠️  No _Toc anchors in {filename} — using whole-document fallback ({len(full_text)} chars)")

    log.info(f"Created {len(sections)} sections with content")
    return sections

# ============================= SMART SECTION CHUNKING =============================

def chunk_sections(
    sections: List[TocSection],
    filename: str,
    max_chunk_size: int = 1500,
    min_chunk_size: int = 100,
    overlap: int = 100
) -> List[TextNode]:
    """
    Convert sections into chunks, splitting large sections if needed.
    """
    chunks = []
    
    splitter = SentenceSplitter(
        chunk_size=max_chunk_size,
        chunk_overlap=overlap
    )
    
    for section in sections:
        text = section.text.strip()
        
        if not text:
            continue
        
        estimated_tokens = len(text) // 4
        
        if estimated_tokens <= max_chunk_size:
            node = TextNode(
                text=text,
                metadata={
                    'file': filename,
                    'section_id': section.toc_id,
                    'section_title': section.title,
                    'breadcrumb': build_breadcrumb(Path(filename).stem, section.title),
                    'slug_url': f"{filename}#{section.toc_id}",
                    'chunk_index': 0,
                    'is_complete_section': True
                }
            )
            node.id_ = stable_node_id(filename, section.toc_id, 0)
            chunks.append(node)
        
        else:
            sub_chunks = splitter.split_text(text)
            
            if len(sub_chunks) > 1:
                log.info(f"Split large section '{section.title}' ({estimated_tokens} tokens) into {len(sub_chunks)} sub-chunks")
            
            for i, sub_text in enumerate(sub_chunks):
                node = TextNode(
                    text=sub_text,
                    metadata={
                        'file': filename,
                        'section_id': section.toc_id,
                        'section_title': section.title,
                        'breadcrumb': build_breadcrumb(Path(filename).stem, section.title),
                        'slug_url': f"{filename}#{section.toc_id}",
                        'chunk_index': i,
                        'is_complete_section': len(sub_chunks) == 1,
                        'total_sub_chunks': len(sub_chunks)
                    }
                )
                node.id_ = stable_node_id(filename, section.toc_id, i)
                chunks.append(node)
    
    return chunks

# ============================= VIQ & SYNONYM ENRICHMENT =============================

def load_viq_rules(rules_path: Path) -> List[Dict]:
    """Load VIQ rules from YAML."""
    if not rules_path.exists():
        log.warning(f"Rules file not found: {rules_path}")
        return []
    
    try:
        data = yaml.safe_load(rules_path.read_text(encoding='utf-8'))
        rules = data.get('viq_rules', [])
        
        for rule in rules:
            rule['patterns'] = [_re.compile(p, _re.I) for p in rule.get('patterns', [])]
        
        log.info(f"Loaded {len(rules)} VIQ rules")
        return rules
    except Exception as e:
        log.error(f"Failed to load VIQ rules: {e}")
        return []

def load_synonyms(rules_path: Path) -> Dict[str, List[str]]:
    """Load synonym groups from YAML."""
    if not rules_path.exists():
        return {}
    
    try:
        data = yaml.safe_load(rules_path.read_text(encoding='utf-8'))
        synonyms = data.get('synonyms', [])
        
        syn_map = {}
        for group in synonyms:
            if isinstance(group, dict):
                terms = group.get('terms', [])
                group_name = group.get('name', terms[0] if terms else 'unknown')
            elif isinstance(group, str):
                terms = [group]
                group_name = group
            else:
                continue
            
            for term in terms:
                pattern = _re.compile(r'\b' + _re.escape(term) + r'\b', _re.I)
                syn_map[term] = {'name': group_name, 'pattern': pattern}
        
        log.info(f"Loaded {len(synonyms)} synonym groups")
        return syn_map
    except Exception as e:
        log.error(f"Failed to load synonyms: {e}")
        return {}

def enrich_chunks(chunks: List[TextNode], viq_rules: List[Dict], synonyms: Dict):
    """Add VIQ and synonym metadata to chunks."""
    for chunk in chunks:
        text_lower = (chunk.text or "").lower()
        
        viq_hints = []
        domain_tags = set()
        
        for rule in viq_rules:
            if 'code' not in rule:
                continue
                
            for rx in rule.get('patterns', []):
                if rx.search(text_lower):
                    viq_hints.append(rule['code'])
                    domain_tags.add(f"viq:{rule['code']}")
                    for tag in rule.get('tags', []):
                        domain_tags.add(tag)
                    break
        
        synonym_hits = []
        for term, info in synonyms.items():
            if info['pattern'].search(text_lower):
                synonym_hits.append(info['name'])
                domain_tags.add(f"syn:{info['name']}")
        
        chunk.metadata['viq_hints'] = ','.join(sorted(set(viq_hints)))
        chunk.metadata['domain_tags'] = ','.join(sorted(domain_tags))
        chunk.metadata['synonym_hits'] = ','.join(sorted(set(synonym_hits)))
    
    return chunks

# ============================= INTEGRITY REPORT =============================

def print_integrity_report(manifest: dict, chroma_collection, docs_dir: Path):
    """Cross-check every HTML file against ChromaDB and print a full integrity report."""
    log.info("=" * 60)
    log.info("📊 INTEGRITY REPORT")
    log.info("=" * 60)

    all_html_files = sorted(docs_dir.glob("*.html"))
    total_files = len(all_html_files)
    not_in_manifest = []
    missing_from_chroma = []
    partial_in_chroma = []
    image_only_stubs = []
    fully_confirmed = 0

    for file_path in all_html_files:
        entry = manifest["files"].get(file_path.name)

        if not entry:
            not_in_manifest.append(file_path.name)
            continue

        expected_chunks = entry.get("chunks", 0)
        is_stub = entry.get("stub", False)

        try:
            file_results = chroma_collection.get(
                where={"file": file_path.name},
                include=["metadatas"]
            )
            actual_count = len(file_results['ids'])
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

    log.info(f"Total HTML files in documents folder : {total_files}")
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
            log.error("   Run with --force flag to fix all missing embeddings.")

    log.info("=" * 60)

# ============================= MAIN INDEXING FUNCTION =============================

async def index_documents(
    client_root: Path,
    rules_path: Path,
    max_chunk_size: int = 1500,
    chunk_overlap: int = 100,
    parallel_workers: int = 6,
    force_reindex: bool = False
):
    """Main indexing function using section-wise chunking."""
    log.info(f"Starting section-wise indexing for {client_root}")
    
    docs_dir = client_root / "documents"
    store_dir = client_root / "index_store"
    chroma_dir = store_dir / "chroma"
    chunks_file = store_dir / "chunks.jsonl"
    manifest_file = store_dir / "manifest.json"
    settings_file = store_dir / "settings.json"
    
    store_dir.mkdir(parents=True, exist_ok=True)
    
    viq_rules = load_viq_rules(rules_path)
    synonyms = load_synonyms(rules_path)
    
    html_files = sorted(docs_dir.glob("*.html"))
    log.info(f"Found {len(html_files)} HTML files")
    
    if not html_files:
        log.error(f"No HTML files found in {docs_dir}")
        return
    
    manifest = load_manifest(manifest_file)
    
    to_process = []
    for p in html_files:
        prev = manifest["files"].get(p.name, {})
        curr_hash = file_hash(p)

        # Reprocess if: forced, hash changed, or previously failed (0 chunks / not embedded)
        needs_retry = prev.get("chunks", 0) == 0 or not prev.get("embedded", False)
        if force_reindex or prev.get("sha256") != curr_hash or needs_retry:
            to_process.append(p)
    
    log.info(f"Processing {len(to_process)} new/changed files")
    
    if not to_process:
        log.info("No files to process. Index is up to date.")
        return
    
    all_chunks = []
    chunks_per_file = {}      # filename -> [chunk_id, ...]
    manifest_updates = {}     # filename -> manifest entry dict

    log.info(f"📄 Processing {len(to_process)} files...")
    for file_path in to_process:
        try:
            html_text = file_path.read_text(encoding="utf-8", errors="replace")

            sections = extract_toc_sections(html_text, file_path.name)

            file_chunks = chunk_sections(
                sections=sections,
                filename=file_path.name,
                max_chunk_size=max_chunk_size,
                min_chunk_size=100,
                overlap=chunk_overlap
            )

            # If no chunks produced (image-only file), create a title stub
            is_stub = False
            if not file_chunks:
                doc_title = Path(file_path.name).stem.replace("_", " ")
                heading_match = _re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html_text, _re.I | _re.S)
                if heading_match:
                    ht = _strip_tags(heading_match.group(1)).strip()
                    if ht and len(ht) > 2:
                        doc_title = ht
                stub_id = stable_slug(doc_title)
                stub_node = TextNode(
                    text=doc_title,
                    metadata={
                        'file': file_path.name,
                        'section_id': stub_id,
                        'section_title': doc_title,
                        'breadcrumb': build_breadcrumb(Path(file_path.name).stem, doc_title),
                        'slug_url': f"{file_path.name}#{stub_id}",
                        'chunk_index': 0,
                        'is_complete_section': True,
                        'stub': True
                    }
                )
                stub_node.id_ = stable_node_id(file_path.name, stub_id, 0)
                file_chunks = [stub_node]
                is_stub = True
                log.warning(f"⚠️  Image-only file — created title stub: {file_path.name}")

            all_chunks.extend(file_chunks)
            chunks_per_file[file_path.name] = [c.id_ for c in file_chunks]
            manifest_updates[file_path.name] = {
                "sha256": file_hash(file_path),
                "mtime": file_path.stat().st_mtime,
                "sections": len(sections),
                "chunks": len(file_chunks),
                "stub": is_stub
            }

            log.info(f"✅ Chunked {file_path.name}: {len(sections)} sections → {len(file_chunks)} chunks{' (title stub)' if is_stub else ''}")

        except Exception as e:
            log.exception(f"❌ Failed to chunk {file_path.name}: {e}")
    
    log.info(f"Total chunks created: {len(all_chunks)}")
    
    if viq_rules or synonyms:
        log.info("Enriching chunks with VIQ and synonym metadata...")
        all_chunks = enrich_chunks(all_chunks, viq_rules, synonyms)
    
    log.info(f"Saving chunks to {chunks_file}")
    with chunks_file.open('w', encoding='utf-8') as f:
        for chunk in all_chunks:
            json_obj = {
                'id_': chunk.id_,
                'text': chunk.text,
                'metadata': chunk.metadata
            }
            f.write(json.dumps(json_obj, ensure_ascii=False) + '\n')
    
    log.info("Setting up embeddings...")
    Settings.embed_model = OpenAIEmbedding(
        model=os.getenv("EMBED_MODEL", "text-embedding-3-large"),
        embed_batch_size=int(os.getenv("EMBED_BATCH", "256")),
        timeout=60
    )
    
    db = chromadb.PersistentClient(path=str(chroma_dir))
    chroma_collection = db.get_or_create_collection("docs")
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    existing_ids = set(chroma_collection.get()['ids'])
    new_chunks = [c for c in all_chunks if c.id_ not in existing_ids]
    
    if new_chunks:
        log.info(f"🔄 Chroma has {len(existing_ids)} chunks. Embedding {len(new_chunks)} new chunks...")
        
        batch_size = 256
        batches = [new_chunks[i:i+batch_size] for i in range(0, len(new_chunks), batch_size)]
        
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
                    insert_batch_size=batch_size
                )
                future.batch_nodes = batch
                futures.append(future)
            
            for future in futures:
                try:
                    index = future.result()
                    index.insert_nodes(future.batch_nodes)
                except Exception as e:
                    log.error(f"Batch embedding failed: {e}")
        
        log.info(f"✅ Upsert complete. Processed {len(new_chunks)} chunks.")
    else:
        log.info(f"✅ All {len(all_chunks)} chunks already in Chroma. No embedding needed.")

    # Confirm which files actually landed in ChromaDB
    confirmed_ids = set(chroma_collection.get()['ids'])

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
    
    settings_file.write_text(json.dumps({
        "embed_model": os.getenv("EMBED_MODEL", "text-embedding-3-large"),
        "chunk_size": max_chunk_size,
        "chunk_overlap": chunk_overlap,
        "chunking_strategy": "section_wise_v2",
        "last_updated": datetime.utcnow().isoformat()
    }, indent=2), encoding='utf-8')
    
    print_integrity_report(manifest, chroma_collection, docs_dir)
    log.info("✅ Indexing complete.")
    log.info(f"📦 Chroma at: {chroma_dir}")
    log.info(f"🧾 Chunks JSONL: {chunks_file}")
    log.info(f"⚙️  Settings: {settings_file}")

# ============================= CLI =============================

def main():
    parser = argparse.ArgumentParser(description="Section-wise HTML indexer (v2)")
    parser.add_argument("--client_root", type=Path, required=True, help="Client root directory")
    parser.add_argument("--rules", type=Path, required=True, help="VIQ rules YAML file")
    parser.add_argument("--chunk_size", type=int, default=1500, help="Max chunk size")
    parser.add_argument("--chunk_overlap", type=int, default=100, help="Chunk overlap")
    parser.add_argument("--parallel_workers", type=int, default=6, help="Parallel workers")
    parser.add_argument("--force", action="store_true", help="Force reindex all files")
    parser.add_argument("--audit", action="store_true", help="Run integrity audit only — no indexing")

    args = parser.parse_args()

    if args.audit:
        client_root = args.client_root
        store_dir = client_root / "index_store"
        chroma_dir = store_dir / "chroma"
        manifest_file = store_dir / "manifest.json"
        manifest = load_manifest(manifest_file)
        db = chromadb.PersistentClient(path=str(chroma_dir))
        chroma_collection = db.get_or_create_collection("docs")
        print_integrity_report(manifest, chroma_collection, client_root / "documents")
        return

    asyncio.run(index_documents(
        client_root=args.client_root,
        rules_path=args.rules,
        max_chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        parallel_workers=args.parallel_workers,
        force_reindex=args.force
    ))

# ============================= ALIASES FOR UNIFIED INDEXER =============================
# These aliases allow indexer_unified_refactored.py to import by its expected names.

def build_sections_from_html(file_path: Path, **kwargs) -> List[TocSection]:
    """Alias: read HTML file and extract _Toc sections."""
    html_text = file_path.read_text(encoding="utf-8", errors="replace")
    return extract_toc_sections(html_text, file_path.name)

def build_chunks_from_sections(
    sections: List[TocSection],
    source_file: str,
    source_type: str = "html",
    max_chunk_size: int = 1500,
    min_chunk_size: int = 100,
    overlap: int = 100,
    **kwargs
) -> List[TextNode]:
    """Alias: wraps chunk_sections with unified-indexer signature."""
    return chunk_sections(
        sections=sections,
        filename=source_file,
        max_chunk_size=max_chunk_size,
        min_chunk_size=min_chunk_size,
        overlap=overlap,
    )


if __name__ == "__main__":
    main()
