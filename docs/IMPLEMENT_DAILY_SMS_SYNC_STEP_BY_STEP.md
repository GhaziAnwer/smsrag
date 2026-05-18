# Implement Daily SMS Document Sync - Step by Step

This guide explains how to implement the daily SMS document sync automation.

The automation script is:

```text
tools/sync_sms_documents.py
```

It pulls the source SMS documents repo, checks supported clients, copies only
new or changed HTML files, and incrementally indexes only changed files.

## 1. Confirm Source Repo Structure

The source repo should look like this:

```text
live-sms-documents/
├── rsms/
│   └── documents/
│       ├── file1.html
│       └── file2.html
├── oceangold/
│   └── documents/
├── supereco/
│   └── documents/
└── almi/
    └── documents/
```

The script only checks:

```text
<source_repo>/<client>/documents/*.html
<source_repo>/<client>/documents/*.htm
```

It does not scan nested folders.

## 2. Confirm Destination Structure

In this SMS RAG project, destination folders should be:

```text
data/<client>/documents/
data/<client>/index_store/
```

Example:

```text
data/oceangold/documents/
data/oceangold/index_store/
```

If a client folder does not exist, the script can create:

```text
data/<client>/documents/
```

## 3. Configure Environment Variables

Do not hardcode the Git token in code.

Set these environment variables on the server:

```bash
export SMS_DOCS_REPO_URL="https://github.com/pandeysury/liveClientDoc.git"
export SMS_DOCS_GIT_TOKEN="your-token"
export SMS_DOCS_REPO_BRANCH="main"
export OPENAI_API_KEY="your-openai-key"
```

Optional overrides:

```bash
export SMS_DOCS_REPO_DIR="/opt/sms-rag/.cache/live-sms-documents"
export SMS_RAG_DATA_DIR="/opt/sms-rag/data"
export SMS_RAG_RULES="/opt/sms-rag/rules.yaml"
export SMS_RAG_CLIENTS="rsms,oceangold,supereco,almi"
export SMS_DOCS_SYNC_LOG="/opt/sms-rag/logs/sms_document_sync.log"
```

Default clients are:

```text
rsms, oceangold, supereco, almi
```

To add a new client later, update:

```bash
export SMS_RAG_CLIENTS="rsms,oceangold,supereco,almi,newclient"
```

## 4. Activate Python Environment

Go to the project root:

```bash
cd /opt/sms-rag
```

Activate the virtual environment:

```bash
source venv/bin/activate
```

Make sure dependencies are installed:

```bash
pip install -r requirements.txt
```

## 5. Run a Dry Run First

Always test with dry run before real execution:

```bash
python3 tools/sync_sms_documents.py --dry-run
```

Dry run will show:

- which repo would be pulled or cloned
- which clients would be checked
- which files would be copied
- which files would be incrementally indexed

Dry run does not copy files and does not index.

## 6. Run the Real Sync Manually

After dry run looks correct:

```bash
python3 tools/sync_sms_documents.py
```

The script will:

1. Clone or pull the source repo.
2. Check each configured client.
3. Compare source HTML files with destination HTML files.
4. Copy only new or changed files.
5. Back up overwritten destination files.
6. Replace only changed file records in Chroma.
7. Preserve unchanged `chunks.jsonl` rows.
8. Update `manifest.json` only after Chroma confirms the changed file chunks.
9. Continue with the next client if one client fails.

## 7. Check Logs

Main log file:

```text
logs/sms_document_sync.log
```

Watch logs live:

```bash
tail -f logs/sms_document_sync.log
```

The log shows:

- client checked
- source folder path
- changed files
- copied files
- destination-only files retained
- indexing started/completed
- errors per client

## 8. Check Backups

Copied document backups:

```text
data/_document_sync_backups/<client>/<timestamp>/
```

Index-state backups:

```text
data/<client>/index_store/auto_index_backups/<timestamp>/
```

Index-state backups include:

- previous `chunks.jsonl`
- old Chroma records for changed files

## 9. Verify Indexing

After sync, check that client index files exist:

```bash
ls -la data/oceangold/index_store/
ls -la data/oceangold/index_store/chroma/
ls -la data/oceangold/index_store/chunks.jsonl
ls -la data/oceangold/index_store/manifest.json
```

You can also test in the UI by asking a question related to an updated document.

## 10. Create a Daily Runner Script

Create a shell runner:

```bash
sudo nano /opt/sms-rag/run-daily-sms-doc-sync.sh
```

Add:

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

## 11. Schedule Daily Cron

Open crontab:

```bash
crontab -e
```

Run once daily at 02:30:

```cron
30 2 * * * /opt/sms-rag/run-daily-sms-doc-sync.sh >> /opt/sms-rag/logs/sms_document_sync.cron.log 2>&1
```

Check cron log:

```bash
tail -f /opt/sms-rag/logs/sms_document_sync.cron.log
```

## 12. Rollback If Needed

The script does not delete files, so rollback is mainly restoring overwritten
files or index state.

Restore a copied document:

```bash
cp data/_document_sync_backups/<client>/<timestamp>/<filename>.html \
   data/<client>/documents/<filename>.html
```

Restore `chunks.jsonl`:

```bash
cp data/<client>/index_store/auto_index_backups/<timestamp>/chunks.jsonl.<timestamp>.bak \
   data/<client>/index_store/chunks.jsonl
```

If Chroma needs deeper recovery, use the Chroma record backup JSON in:

```text
data/<client>/index_store/auto_index_backups/<timestamp>/
```

For severe issues, stop cron first:

```bash
crontab -e
```

Comment the sync line:

```cron
# 30 2 * * * /opt/sms-rag/run-daily-sms-doc-sync.sh >> /opt/sms-rag/logs/sms_document_sync.cron.log 2>&1
```

## 13. Safety Notes

- Token is read from environment variables.
- No destination files are deleted.
- Renamed files are treated as new files.
- Old destination files from renamed/deleted source files are retained.
- Only changed files are copied.
- Only changed file Chroma records are replaced.
- Unchanged `chunks.jsonl` rows are preserved.
- `manifest.json` updates only after Chroma confirmation.
- If one client fails, other clients continue.
- Lock file prevents overlapping sync runs.
