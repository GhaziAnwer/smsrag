# Daily SMS Document Sync

This automation pulls the source SMS documents repository, copies only new or
changed HTML files for supported clients, and incrementally indexes only the
files copied for clients that changed.

It does not delete destination files and it does not change the existing
section-wise parser/chunker/enrichment logic from `tools/indexer_section_wise.py`.

## Supported Clients

Default clients are configured in `tools/sync_sms_documents.py`:

```text
rsms, oceangold, supereco, almi
```

Override or extend them without code changes:

```bash
export SMS_RAG_CLIENTS="rsms,oceangold,supereco,almi,newclient"
```

The source repo is expected to contain:

```text
<repo>/<client>/documents/*.html
<repo>/<client>/documents/*.htm
```

The destination is:

```text
data/<client>/documents/
```

## Required Environment

Do not hardcode the Git token. Keep it in the environment.

Recommended:

```bash
export SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git"
export SMS_DOCS_GIT_TOKEN="your-token"
export SMS_DOCS_REPO_BRANCH="main"
export OPENAI_API_KEY="your-openai-key"
```

When `SMS_DOCS_GIT_TOKEN` is provided separately, the script authenticates
Git with a temporary HTTP header so the token is not written into the cloned
repo's remote URL.

Optional path overrides:

```bash
export SMS_DOCS_REPO_DIR="/opt/sms-rag/.cache/live-sms-documents"
export SMS_RAG_DATA_DIR="/opt/sms-rag/data"
export SMS_RAG_RULES="/opt/sms-rag/rules.yaml"
export SMS_DOCS_SYNC_LOG="/opt/sms-rag/logs/sms_document_sync.log"
```

## Dry Run

Always test with dry run first:

```bash
cd /opt/sms-rag
source venv/bin/activate

python3 tools/sync_sms_documents.py --dry-run
```

This shows which clients and files would change without copying or indexing.

## Manual Run

```bash
cd /opt/sms-rag
source venv/bin/activate

python3 tools/sync_sms_documents.py
```

Logs are written to:

```text
logs/sms_document_sync.log
```

Overwritten destination files are backed up under:

```text
data/_document_sync_backups/<client>/<timestamp>/
```

Index-state backups are written under:

```text
data/<client>/index_store/auto_index_backups/<timestamp>/
```

These include the previous `chunks.jsonl` and old Chroma records for changed
files before replacement.

## Daily Cron

Create a runner script outside the repo, for example:

```bash
sudo nano /opt/sms-rag/run-daily-sms-doc-sync.sh
```

Example content:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /opt/sms-rag
source /opt/sms-rag/venv/bin/activate

export SMS_DOCS_REPO_URL="${SMS_DOCS_REPO_URL:?SMS_DOCS_REPO_URL is required}"
export SMS_DOCS_GIT_TOKEN="${SMS_DOCS_GIT_TOKEN:?SMS_DOCS_GIT_TOKEN is required}"
export OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
export SMS_DOCS_REPO_BRANCH="${SMS_DOCS_REPO_BRANCH:-main}"
export SMS_RAG_CLIENTS="${SMS_RAG_CLIENTS:-rsms,oceangold,supereco,almi}"

python3 tools/sync_sms_documents.py
```

Make it executable:

```bash
sudo chmod +x /opt/sms-rag/run-daily-sms-doc-sync.sh
```

Edit cron:

```bash
crontab -e
```

Run once daily at 02:30:

```cron
30 2 * * * /opt/sms-rag/run-daily-sms-doc-sync.sh >> /opt/sms-rag/logs/sms_document_sync.cron.log 2>&1
```

## Safety Behavior

- Only configured clients are checked.
- Only top-level `.html` and `.htm` files are copied, matching the existing indexer.
- Files are copied only when content hash differs.
- Existing destination files are backed up before replacement.
- Destination files are never deleted.
- Destination-only files are logged and retained.
- Only changed files have their old Chroma records replaced.
- `chunks.jsonl` rows for unchanged files are preserved.
- `manifest.json` is updated only after Chroma confirms the changed file chunks exist.
- If one client fails, the next client still runs.
- A lock file prevents overlapping sync runs.
