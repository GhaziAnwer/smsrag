# Production Deployment Configuration

## Updated Files

### 1. nginx.conf
Updated to align with production codebase structure at `/opt/sms-rag/`

**Key Changes:**
- **Dynamic Tenant API**: `/<client>/api/*` routes to backend for multi-tenant support
- **Global API Endpoints**: `/api/feedback` and `/api/dashboard` for tenant-agnostic features
- **Dynamic Document Serving**: `/<client>/docs/*` serves from `/opt/sms-rag/data/<client>/documents/`
- **Static Files**: All static assets served from `/opt/sms-rag/static/`
- **Removed**: Hard-coded tenant-specific document locations (rsms, maran, oceangold, etc.)
- **Removed**: SIRE VIQ related configurations (port 8001)

**URL Structure:**
```
https://chatai.sl-sail.com/rsms/api/ask          → Backend: /{client_id}/api/ask
https://chatai.sl-sail.com/maran/api/history     → Backend: /{client_id}/api/history
https://chatai.sl-sail.com/rsms/docs/manual.pdf  → File: /opt/sms-rag/data/rsms/documents/manual.pdf
https://chatai.sl-sail.com/api/feedback/submit   → Backend: /api/feedback/submit
https://chatai.sl-sail.com/                      → Static: /opt/sms-rag/static/index.html
```

### 2. Dockerfile
Enhanced for production deployment with proper environment configuration.

**Key Changes:**
- Added `DOCKER_CONTAINER=true` environment variable for auto-detection
- Set `BASE_DIR=/app/data` explicitly
- Created both `/app/data` and `/app/docs` directories
- Specified `--workers 1` for uvicorn (adjust based on server capacity)
- Improved layer caching and build optimization

### 3. docker-compose.yml
Updated volume mounts and environment configuration.

**Key Changes:**
- **Data Volume**: `/opt/sms-rag/data:/app/data:rw` (read-write for index_store)
- **Docs Volume**: `/opt/sms-rag/data:/app/docs:ro` (read-only for documents)
- **Database Mounts**: Added chat_history.db, feedback.db, query_logs.db
- **Environment Variables**:
  - `ENVIRONMENT=production`
  - `DOCKER_CONTAINER=true`
  - `DEFAULT_CLIENT_ID=rsms`
  - `ALLOW_ORIGINS` with specific domains
  - `LOG_LEVEL=INFO`
- **Network**: Added dedicated bridge network `sms-rag-network`

## Directory Structure

```
/opt/sms-rag/
├── app/                          # Application code
│   ├── main.py                   # FastAPI app with multi-tenant routing
│   ├── config.py                 # Auto-detects Docker vs local paths
│   ├── routers/                  # API endpoints
│   │   ├── query.py              # /{client_id}/api/* endpoints
│   │   ├── feedback.py           # /api/feedback/* endpoints
│   │   └── dashboard.py          # /api/dashboard/* endpoints
│   └── ...
├── static/                       # Frontend files
│   ├── index.html
│   ├── login.html
│   ├── dashboard.html
│   ├── feedback-dashboard.html
│   └── feedback-integration.js
├── data/                         # Tenant data (mounted in Docker)
│   ├── rsms/
│   │   ├── index_store/          # Vector DB & chunks
│   │   └── documents/            # PDF files
│   ├── maran/
│   │   ├── index_store/
│   │   └── documents/
│   └── ...
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── chat_history.db
├── feedback.db
├── query_logs.db
└── .env
```

## Deployment Steps

### 1. Pre-deployment Checklist
```bash
# Ensure data directory exists with proper structure
ls -la /opt/sms-rag/data/rsms/index_store/
ls -la /opt/sms-rag/data/rsms/documents/

# Verify .env file has required variables
cat /opt/sms-rag/.env
```

### 2. Build and Deploy
```bash
cd /opt/sms-rag

# Build Docker image
docker-compose build

# Start services
docker-compose up -d

# Check logs
docker-compose logs -f app
```

### 3. Update Nginx
```bash
# Test nginx configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

### 4. Verify Deployment
```bash
# Check container status
docker-compose ps

# Test health endpoint
curl http://localhost:8000/health

# Test tenant API
curl https://chatai.sl-sail.com/rsms/api/health

# Check logs
docker-compose logs --tail=100 app
```

## Environment Variables (.env)

Required variables in `/opt/sms-rag/.env`:

```env
# OpenAI Configuration
OPENAI_API_KEY=sk-...

# Application Settings
ENVIRONMENT=production
DEFAULT_CLIENT_ID=rsms
LOG_LEVEL=INFO

# CORS Settings
ALLOW_ORIGINS=https://chatai.sl-sail.com,https://sailerp.sl-sail.com,https://erp.sl-sail.com

# Optional: Override auto-detection
# BASE_DIR=/app/data
```

## Monitoring

### Check Application Logs
```bash
docker-compose logs -f app
```

### Check Nginx Logs
```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Health Check
```bash
# Container health
docker inspect sms-rag-app | grep -A 10 Health

# Application health
curl http://localhost:8000/health
```

## Troubleshooting

### Container Won't Start
```bash
# Check logs
docker-compose logs app

# Verify volumes exist
ls -la /opt/sms-rag/data/

# Check permissions
sudo chown -R 1000:1000 /opt/sms-rag/data/
```

### 404 on Document URLs
```bash
# Verify document path in container
docker exec sms-rag-app ls -la /app/docs/rsms/documents/

# Check nginx document alias
sudo nginx -T | grep "docs"
```

### API Not Responding
```bash
# Check if port 8000 is listening
sudo netstat -tlnp | grep 8000

# Test direct container access
curl http://localhost:8000/rsms/api/health

# Check nginx proxy
curl -I https://chatai.sl-sail.com/rsms/api/health
```

## Rollback Procedure

If issues occur:

```bash
# Stop new container
docker-compose down

# Restore previous configuration
git checkout HEAD~1 nginx.conf docker-compose.yml Dockerfile

# Restart with old config
docker-compose up -d

# Reload nginx
sudo systemctl reload nginx
```

## Notes

- The application auto-detects Docker environment via `DOCKER_CONTAINER=true`
- All tenant data must be in `/opt/sms-rag/data/<client>/`
- Documents are served directly by nginx for better performance
- API calls are proxied to the FastAPI backend
- SSL certificates are managed by Certbot (Let's Encrypt)
