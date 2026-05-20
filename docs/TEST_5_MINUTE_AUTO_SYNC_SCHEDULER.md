# Test Auto Sync Scheduler Every 5 Minutes

Use this only for testing the automatic document pull and indexing flow.

For production, change the schedule back to daily after testing.

## What This Scheduler Does

Every 5 minutes it runs:

```text
/opt/smsrag/scripts/run_sms_document_sync.sh
```

That script:

1. Goes to `/opt/smsrag`.
2. Loads `/opt/smsrag/.env`.
3. Pulls the configured document repository.
4. Checks configured clients.
5. Copies only new/changed HTML files.
6. Indexes only clients where files changed.
7. Skips indexing when nothing changed.
8. Restarts `sms-rag-app` only if at least one client was indexed.
9. Writes logs under `/opt/sms-rag-index-data/_document_sync_logs/`.

The sync script already has a lock file, so if one run is still active, the next run will not overlap.

Why the restart is included:

- The FastAPI app caches retriever bundles in memory.
- Cron updates Chroma/chunks on disk.
- Restarting the app after a real index update ensures question answering uses the latest index.
- If no files changed and no indexing happened, the wrapper does not restart Docker.

## Step 1: Check Script Permission

On the server:

```bash
cd /opt/smsrag
chmod +x scripts/run_sms_document_sync.sh
```

## Step 2: Test The Script Manually

Run once manually before adding cron:

```bash
cd /opt/smsrag
./scripts/run_sms_document_sync.sh
```

Expected result:

```text
Completed SMS document sync with 0 failure(s)
```

If no files changed, this is also correct:

```text
Changed clients: none
Indexed clients: none
```

## Step 3: Add 5-Minute Cron

Open crontab:

```bash
crontab -e
```

Add this line:

```cron
*/5 * * * * /opt/smsrag/scripts/run_sms_document_sync.sh >> /opt/sms-rag-index-data/_document_sync_logs/cron-5min.log 2>&1
```

Save and exit.

## Step 4: Confirm Cron Is Added

```bash
crontab -l
```

You should see:

```cron
*/5 * * * * /opt/smsrag/scripts/run_sms_document_sync.sh >> /opt/sms-rag-index-data/_document_sync_logs/cron-5min.log 2>&1
```

## Step 5: Watch Logs

Cron wrapper log:

```bash
tail -f /opt/sms-rag-index-data/_document_sync_logs/cron-5min.log
```

Main sync log:

```bash
tail -f /opt/sms-rag-index-data/_document_sync_logs/sms_document_sync.log
```

## Step 6: Test Functionality

To test:

1. Add or update an HTML file in the source repo.
2. Commit and push to the configured branch.
3. Wait up to 5 minutes.
4. Check logs.

You should see:

```text
Pulled source repo
Changed clients: <client>
Indexed clients: <client>
Index changed; restarting sms-rag-app
Completed SMS document sync with 0 failure(s)
```

If no document changed, you should see:

```text
Changed clients: none
Indexed clients: none
No clients indexed; app restart not needed
Completed SMS document sync with 0 failure(s)
```

That means the scheduler is working and correctly skipping duplicate indexing.

## Step 7: Remove 5-Minute Test Cron After Testing

Open crontab:

```bash
crontab -e
```

Remove this line:

```cron
*/5 * * * * /opt/smsrag/scripts/run_sms_document_sync.sh >> /opt/sms-rag-index-data/_document_sync_logs/cron-5min.log 2>&1
```

## Step 8: Add Daily Production Cron

After testing, use daily schedule instead:

```cron
0 2 * * * /opt/smsrag/scripts/run_sms_document_sync.sh >> /opt/sms-rag-index-data/_document_sync_logs/cron.log 2>&1
```

This runs every day at 2:00 AM server time.

## Useful Checks

Check cron service:

```bash
sudo systemctl status cron --no-pager
```

Restart cron if needed:

```bash
sudo systemctl restart cron
```

Check app container:

```bash
docker ps | grep sms-rag-app
```

Check app:

```bash
curl http://127.0.0.1:8010/
```
