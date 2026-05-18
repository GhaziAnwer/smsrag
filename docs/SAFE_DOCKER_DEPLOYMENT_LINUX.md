# Safe Docker Deployment On Existing Linux/Nginx Server

This guide deploys SMS RAG without stopping existing Docker sites.

Given your current server, host port `8000` is already used by `crew_api`, so SMS RAG should run on host port `8010`.

## 1. Go To Project

```bash
cd /opt/smsrag
```

Pull/copy the latest project files first, including:

- `docker-compose.yml`
- `Dockerfile`
- `scripts/deploy_smsrag_docker.sh`
- `deploy/nginx_smsrag_safe_locations.conf`
- `tools/sync_sms_documents.py` if you will run auto-sync/indexing on the server

## 2. Create Production Env

Create `/opt/smsrag/.env`:

```bash
nano /opt/smsrag/.env
```

Minimum values:

```env
OPENAI_API_KEY=your_openai_key_here
SMSRAG_HOST_PORT=8010
SMSRAG_DATA_DIR=/opt/sms-rag-index-data
BASE_DIR=/app/data
DEFAULT_CLIENT_ID=rsms
ALLOW_ORIGINS=*
```

Do not paste GitHub/GitLab tokens into code. Put repo tokens only in `.env` if needed.

## 3. Prepare Data

The container expects client data here on the server:

```text
/opt/sms-rag-index-data/<client>/documents/
/opt/sms-rag-index-data/<client>/index_store/
```

Example:

```text
/opt/sms-rag-index-data/rsms/documents/
/opt/sms-rag-index-data/rsms/index_store/chunks.jsonl
/opt/sms-rag-index-data/rsms/index_store/chroma/
```

If data is not indexed yet, run the sync/indexing script on the server before starting production traffic:

```bash
cd /opt/smsrag
set -a
. ./.env
set +a
python tools/sync_sms_documents.py --data-dir /opt/sms-rag-index-data
```

By default, this writes runtime files under the data directory:

```text
/opt/sms-rag-index-data/_document_sync_logs/sms_document_sync.log
/opt/sms-rag-index-data/_document_sync_logs/sms_document_sync.lock
/opt/sms-rag-index-data/_document_sync_backups/
/opt/sms-rag-index-data/_document_sync_repo/live-sms-documents/
```

If you get a permission error, fix ownership for only this app data folder:

```bash
sudo mkdir -p /opt/sms-rag-index-data
sudo chown -R ubuntu:ubuntu /opt/sms-rag-index-data
```

## 4. Check Port Safety

Confirm `8010` is free:

```bash
ss -tlnp | grep ':8010' || echo "8010 is free"
docker ps
```

Do not use `8000`; it is already used by an existing container.

## 5. Start Only SMS RAG Container

```bash
cd /opt/smsrag
./scripts/deploy_smsrag_docker.sh
```

This script only runs this compose project. It does not stop other containers.

Expected check:

```bash
curl http://127.0.0.1:8010/
```

Response should include:

```json
HTML from the SMS RAG app.
```

## 6. Add Nginx Safely

Backup current Nginx config:

```bash
sudo cp /etc/nginx/conf.d/safelanes.conf /etc/nginx/conf.d/safelanes.conf.bak.$(date +%Y%m%d%H%M%S)
```

Open the config:

```bash
sudo nano /etc/nginx/conf.d/safelanes.conf
```

Paste the contents of:

```text
/opt/smsrag/deploy/nginx_smsrag_safe_locations.conf
```

inside the existing `server { ... }` block.

Important:

- Keep existing `/graphagent-new/`, `/viq-rag/`, `/graph-ai/`, and `/graph-ai-V2/` locations unchanged.
- Keep existing `/rsms/docs/` alias unchanged if it points to `/opt/sms-rag-index-data/rsms/documents/`.
- Do not add a broad `location /` unless you intentionally want SMS RAG to own every unknown root path.

Test Nginx before reload:

```bash
sudo nginx -t
```

Reload only if the test passes:

```bash
sudo systemctl reload nginx
```

## 7. Verify

SMS RAG:

```bash
curl http://127.0.0.1:8010/
curl http://devsmsragai.com/sms-rag-health
```

Existing apps should still show running:

```bash
docker ps
```

Check important existing URLs manually:

- `/graphagent-new/`
- `/viq-rag/`
- `/graph-ai/`
- `/graph-ai-V2/`

## 8. Rollback

Stop only SMS RAG:

```bash
cd /opt/smsrag
docker compose down
```

Restore Nginx backup:

```bash
sudo cp /etc/nginx/conf.d/safelanes.conf.bak.YYYYMMDDHHMMSS /etc/nginx/conf.d/safelanes.conf
sudo nginx -t
sudo systemctl reload nginx
```

Existing Docker sites do not need to be stopped for rollback.
