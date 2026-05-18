# Local Auto Indexing Test Guide

This guide explains how to test the daily document sync and auto indexing flow
locally.

Automation script:

```text
tools/sync_sms_documents.py
```

Core indexing logic reused from:

```text
tools/indexer_section_wise.py
```

## What This Test Proves

The local test verifies:

- GitHub repo can be pulled.
- Client folders are checked.
- New or changed HTML files are copied into `data/<client>/documents/`.
- Only changed files are indexed.
- Old Chroma records for changed files are replaced.
- `chunks.jsonl` rows remain consistent.
- `manifest.json` updates only after Chroma confirms chunks.
- Second run becomes a no-op if nothing changed.

## 1. Activate Virtual Environment

From project root:

```bash
cd /home/sheshmani/Desktop/smsrag
source venv/bin/activate
```

## 2. Confirm Required Environment

The GitHub repo is public-readable currently, so token is not required for local
testing.

Set the repo and branch:

```bash
export SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git"
export SMS_DOCS_REPO_BRANCH="main"
export SMS_DOCS_REPO_DIR=".cache/liveClientDoc"
```

Make sure OpenAI key is available for embedding:

```bash
set -a
. ./.env
set +a
```

Check:

```bash
test -n "$OPENAI_API_KEY" && echo "OPENAI_API_KEY is set"
```

## 3. Compile Check

```bash
python3 -m compileall tools/sync_sms_documents.py
```

Expected:

```text
Compiling 'tools/sync_sms_documents.py'...
```

No error means syntax is fine.

## 4. Help Check

```bash
python3 tools/sync_sms_documents.py --help
```

Expected:

```text
Sync SMS HTML documents and index changed clients.
```

## 5. Clone or Pull Source Repo

The script can clone automatically, but you can also prepare it manually:

```bash
mkdir -p .cache

if [ -d .cache/liveClientDoc/.git ]; then
  git -C .cache/liveClientDoc pull --ff-only
else
  git clone --single-branch --branch main \
    https://github.com/pandeysury/liveClientDoc.git \
    .cache/liveClientDoc
fi
```

Check repo client folders:

```bash
find .cache/liveClientDoc -maxdepth 2 -type d -name documents -printf '%h\n' \
  | xargs -r -n1 basename \
  | sort
```

Current expected:

```text
almi
oceangold
rsms
supereco
```

## 6. Dry Run

Dry run shows what would happen without copying or indexing:

```bash
SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git" \
SMS_DOCS_REPO_BRANCH="main" \
SMS_DOCS_REPO_DIR=".cache/liveClientDoc" \
python3 tools/sync_sms_documents.py --dry-run
```

Expected output includes:

```text
Clients: rsms, oceangold, supereco, almi
```

If `data/` is empty, expected:

```text
[rsms] Would copy: ...
[rsms] Would incrementally index files: ...
```

## 7. Real Local Run

Run real sync and indexing:

```bash
set -a
. ./.env
set +a

SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git" \
SMS_DOCS_REPO_BRANCH="main" \
SMS_DOCS_REPO_DIR=".cache/liveClientDoc" \
./venv/bin/python tools/sync_sms_documents.py
```

Expected if `data/` is empty:

```text
[rsms] Copied: ...
[rsms] Starting safe incremental indexing
Confirmed ... Chroma chunks
Updated chunks JSONL
Updated manifest only after Chroma confirmation
Completed SMS document sync with 0 failure(s)
```

Note: Chroma may print telemetry warnings. These are non-fatal if the final
summary says `0 failure(s)`.

## 8. Verify Files Created

```bash
find data -maxdepth 3 -type f \
  \( -iname '*.html' -o -iname '*.htm' -o -name 'chunks.jsonl' -o -name 'manifest.json' -o -name 'settings.json' \) \
  -printf '%P\n' \
  | sort
```

Expected after current repo data:

```text
rsms/documents/1.1 Introduction Management Leadership & Accountability.html
rsms/documents/1.1_z_Appendix_1 Risk and Opportunities.html
rsms/documents/1.2 Vision, Mission, Policies & Objectives.html
rsms/index_store/chunks.jsonl
rsms/index_store/manifest.json
rsms/index_store/settings.json
```

## 9. Verify Manifest and Chunks

```bash
wc -l data/rsms/index_store/chunks.jsonl

./venv/bin/python - <<'PY'
import json
from pathlib import Path

manifest = json.loads(Path("data/rsms/index_store/manifest.json").read_text())
print("manifest_files", len(manifest.get("files", {})))

for name, entry in sorted(manifest.get("files", {}).items()):
    print(name, "chunks=", entry.get("chunks"), "embedded=", entry.get("embedded"))
PY
```

Expected from current repo:

```text
22 data/rsms/index_store/chunks.jsonl
manifest_files 3
... embedded= True
```

## 10. Verify Chroma

```bash
./venv/bin/python - <<'PY'
import chromadb

client = chromadb.PersistentClient(path="data/rsms/index_store/chroma")
col = client.get_or_create_collection("docs")

print("chroma_count", col.count())

for fname in [
    "1.1 Introduction Management Leadership & Accountability.html",
    "1.1_z_Appendix_1 Risk and Opportunities.html",
    "1.2 Vision, Mission, Policies & Objectives.html",
]:
    result = col.get(where={"file": fname}, include=[])
    print(fname, len(result["ids"]))
PY
```

Expected:

```text
chroma_count 22
1.1 Introduction Management Leadership & Accountability.html 8
1.1_z_Appendix_1 Risk and Opportunities.html 1
1.2 Vision, Mission, Policies & Objectives.html 13
```

## 11. Run Again To Confirm No Duplicate Indexing

Run the same command again:

```bash
set -a
. ./.env
set +a

SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git" \
SMS_DOCS_REPO_BRANCH="main" \
SMS_DOCS_REPO_DIR=".cache/liveClientDoc" \
./venv/bin/python tools/sync_sms_documents.py
```

Expected:

```text
No new/changed HTML files
Skipping indexing because nothing changed
Changed clients: none
Indexed clients: none
Completed SMS document sync with 0 failure(s)
```

## 12. Test Changed-File Replacement Safely

This test uses `/tmp`, not your real `data/` folder.

```bash
set -a
. ./.env
set +a

work=/tmp/sms-sync-e2e-verify
rm -rf "$work"
mkdir -p "$work/repo/rsms/documents" "$work/data" "$work/backups"

cat > "$work/repo/rsms/documents/test.html" <<'HTML'
<html><body><a name="_Toc100"></a><h1>Test Section</h1><p>Initial safety management test content for indexing.</p></body></html>
HTML

./venv/bin/python tools/sync_sms_documents.py \
  --repo-dir "$work/repo" \
  --data-dir "$work/data" \
  --clients rsms \
  --rules rules.yaml \
  --backup-dir "$work/backups" \
  --log-file "$work/run1.log" \
  --lock-file "$work/run1.lock" \
  --skip-pull
```

Now update the same file:

```bash
cat > "$work/repo/rsms/documents/test.html" <<'HTML'
<html><body><a name="_Toc100"></a><h1>Test Section Updated</h1><p>Updated safety management test content for replacement indexing.</p></body></html>
HTML

./venv/bin/python tools/sync_sms_documents.py \
  --repo-dir "$work/repo" \
  --data-dir "$work/data" \
  --clients rsms \
  --rules rules.yaml \
  --backup-dir "$work/backups" \
  --log-file "$work/run2.log" \
  --lock-file "$work/run2.lock" \
  --skip-pull
```

Verify replacement did not duplicate records:

```bash
./venv/bin/python - <<'PY'
import chromadb, json
from pathlib import Path

root = Path("/tmp/sms-sync-e2e-verify/data/rsms/index_store")
manifest = json.loads((root / "manifest.json").read_text())
rows = (root / "chunks.jsonl").read_text().splitlines()
col = chromadb.PersistentClient(path=str(root / "chroma")).get_or_create_collection("docs")
records = col.get(where={"file": "test.html"}, include=["documents"])

print("manifest_files", len(manifest["files"]))
print("chunks_jsonl_rows", len(rows))
print("chroma_count", col.count())
print("test_file_records", len(records["ids"]))
print("updated_content_present", "Updated safety management" in records["documents"][0])
PY
```

Expected:

```text
manifest_files 1
chunks_jsonl_rows 1
chroma_count 1
test_file_records 1
updated_content_present True
```

## 13. Where Logs Are Written

Default script log:

```text
logs/sms_document_sync.log
```

For test commands above, logs may be written to:

```text
/tmp/sms_sync_*.log
/tmp/sms-sync-e2e-verify/run1.log
/tmp/sms-sync-e2e-verify/run2.log
```

## 14. Rollback Local Data

If you want to clear local generated data:

```bash
rm -rf data/rsms
```

If you moved old data folders into backup, restore with:

```bash
mv .cache/data-client-backups/<timestamp>/<client> data/<client>
```

Example:

```bash
mv .cache/data-client-backups/20260514T104228Z/maran data/maran
```

## 15. Final Success Criteria

Your local test is successful when:

- script exits with code `0`
- final summary says `0 failure(s)`
- changed files are copied
- Chroma count matches `chunks.jsonl` row count
- manifest entries show `embedded=True`
- second run skips indexing
- changed-file replacement test keeps one record, not duplicates

