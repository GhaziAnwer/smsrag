import sys, os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import chromadb

client = chromadb.PersistentClient(path=r"D:\indexstores\dynagas\index_store\chroma")
col = client.get_collection("docs")
print(f"Total chunks in ChromaDB: {col.count()}")

# Get 3 samples from start
result = col.get(limit=3, include=["metadatas", "documents"])
required = {"file", "slug_url", "breadcrumb", "section_title"}

for i in range(3):
    print(f"\n--- Chunk {i+1} ---")
    print(f"ID: {result['ids'][i]}")
    meta = result['metadatas'][i]
    for k, v in sorted(meta.items()):
        print(f"  {k}: {v}")
    doc = result['documents'][i]
    print(f"  text_preview: {doc[:150]}...")
    missing = required - set(meta.keys())
    if missing:
        print(f"  !! MISSING: {missing}")
    # Check .html
    f = meta.get("file", "")
    if not f.endswith(".html"):
        print(f"  !! file does NOT end with .html: {f}")
    # Check slug_url format
    slug = meta.get("slug_url", "")
    if "#" not in slug:
        print(f"  !! slug_url missing #anchor: {slug}")
    # Check breadcrumb
    bc = meta.get("breadcrumb", "")
    if "> Document" in bc:
        print(f"  !! breadcrumb has '> Document': {bc}")
    if "<" in bc:
        print(f"  !! breadcrumb has HTML tags: {bc}")

# Also check 3 from middle
result2 = col.get(limit=3, offset=1500, include=["metadatas"])
print(f"\n--- 3 more from offset 1500 ---")
for i in range(min(3, len(result2['ids']))):
    meta = result2['metadatas'][i]
    print(f"  file={meta.get('file','')} | slug_url={meta.get('slug_url','')} | breadcrumb={meta.get('breadcrumb','')[:80]} | section_title={meta.get('section_title','')[:60]}")
