import chromadb
c = chromadb.PersistentClient(path=r"D:\indexstores\dynagas\index_store\chroma")
col = c.get_collection("docs")
r = col.get(include=["metadatas"])
files = set(m["file"] for m in r["metadatas"])
print(f"Unique files in ChromaDB: {len(files)}")

# Count by extension
from collections import Counter
exts = Counter()
chunks_per_file = Counter()
for m in r["metadatas"]:
    f = m["file"]
    ext = f.rsplit(".", 1)[-1].lower()
    exts[ext] += 1
    chunks_per_file[f] += 1

print(f"\nChunks by file type:")
for ext, count in exts.most_common():
    print(f"  .{ext}: {count} chunks")

print(f"\nTop 10 files by chunk count:")
for f, count in chunks_per_file.most_common(10):
    print(f"  {count:5d}  {f}")

print(f"\nBottom 5 files by chunk count:")
for f, count in chunks_per_file.most_common()[-5:]:
    print(f"  {count:5d}  {f}")
