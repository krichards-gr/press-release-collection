# Deployment Checklist

Quick checklist for deploying the press release collection pipeline to Google Cloud Run.

---

## ⚡ Quick Start (Automated)

### Option 1: Use PowerShell Script (Windows)

```powershell
.\deploy.ps1
```

### Option 2: Use Bash Script (Mac/Linux)

```bash
chmod +x deploy.sh
./deploy.sh
```

**What the script does**:
- ✅ Enables required GCP APIs
- ✅ Creates BigQuery dataset
- ✅ Stores Bright Data credentials in Secret Manager
- ✅ Deploys to Cloud Run from GitHub
- ✅ Creates 3 Cloud Scheduler jobs (midnight, noon, 4pm EST)
- ✅ Sets up service account and permissions

**Time**: ~10-15 minutes for first deployment

---

## 📋 Manual Deployment (Step-by-Step)

See [DEPLOY_FROM_GITHUB.md](DEPLOY_FROM_GITHUB.md) for detailed instructions.

---

## ✅ Pre-Deployment Checklist

Before running the deployment:

- [ ] **GCP Project Created**
  - Project ID: ________________
  - Billing enabled

- [ ] **GitHub Repository Ready**
  - Code pushed to GitHub
  - Repository URL: ________________

- [ ] **Bright Data Credentials**
  - Proxy URL: ________________
  - Format: `http://brd-customer-XXX-zone-XXX:PASSWORD@brd.superproxy.io:33335`

- [ ] **gcloud CLI Installed & Authenticated**
  ```bash
  gcloud --version
  gcloud auth login
  gcloud config set project YOUR_PROJECT_ID
  ```

- [ ] **Reference Data Ready**
  - Company list with pressroom URLs
  - File: `inputs/reference_data.csv`

---

## 🔧 Configuration

### Required Environment Variables

Set in Cloud Run deployment:

| Variable | Value | Source |
|----------|-------|--------|
| `BRIGHT_DATA_PROXY_URL` | Your proxy URL | Secret Manager |
| `BIGQUERY_DATASET` | `pressure_monitoring` | Environment |
| `GCP_PROJECT` | Your project ID | Environment |

### Cloud Scheduler Jobs

| Job Name | Schedule | Time (EST) | Cron |
|----------|----------|------------|------|
| `press-release-midnight` | Daily | Midnight | `0 5 * * *` |
| `press-release-noon` | Daily | Noon | `0 17 * * *` |
| `press-release-4pm` | Daily | 4 PM | `0 21 * * *` |

**Note**: Cron uses UTC. EST times are converted (EST = UTC - 5 hours).

---

## 🧪 Post-Deployment Testing

### 1. Test Cloud Run Service

```bash
SERVICE_URL=$(gcloud run services describe press-release-collector \
  --region=us-central1 --format='value(status.url)')

# Quick test (no scraping)
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-10",
    "end_date": "2026-02-11",
    "skip_scraping": true
  }'
```

**Expected**: 200 OK with JSON response

### 2. Test Full Pipeline

```bash
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-10",
    "end_date": "2026-02-11"
  }'
```

**Expected**: Takes 5-15 minutes, returns stats

### 3. Test Scheduler Job

```bash
gcloud scheduler jobs run press-release-midnight --location=us-central1
```

**Check logs**:
```bash
gcloud run services logs tail press-release-collector --region=us-central1
```

### 4. Verify BigQuery Tables

```sql
-- Check collection runs
SELECT * FROM `pressure_monitoring.collection_runs`
ORDER BY start_timestamp DESC LIMIT 5;

-- Check collected articles
SELECT COUNT(*) FROM `pressure_monitoring.collected_articles`;

-- Check enrichments
SELECT COUNT(*) FROM `pressure_monitoring.article_enrichments`;
```

---

## 📊 Monitoring Dashboard

### Key Metrics to Track

1. **Collection Runs**
   ```sql
   SELECT
     DATE(start_timestamp) as date,
     COUNT(*) as runs,
     SUM(urls_collected) as total_urls,
     SUM(articles_scraped) as total_articles,
     COUNTIF(status = 'completed') as successful_runs,
     COUNTIF(status = 'failed') as failed_runs
   FROM `pressure_monitoring.collection_runs`
   WHERE start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
   GROUP BY date
   ORDER BY date DESC;
   ```

2. **Query Efficiency (Cost Savings)**
   ```sql
   SELECT
     run_id,
     queries_count as executed,
     urls_collected,
     ROUND(urls_collected / queries_count, 2) as urls_per_query
   FROM `pressure_monitoring.collection_runs`
   WHERE status = 'completed' AND queries_count > 0
   ORDER BY start_timestamp DESC
   LIMIT 10;
   ```

3. **Scraper Performance**
   ```sql
   SELECT
     scraper_used,
     COUNT(*) as articles_count,
     ROUND(COUNT(*) / (SELECT COUNT(*) FROM `pressure_monitoring.collected_articles`) * 100, 2) as percentage
   FROM `pressure_monitoring.collected_articles`
   WHERE scraper_used IS NOT NULL
   GROUP BY scraper_used
   ORDER BY articles_count DESC;
   ```

---

## 🚨 Common Issues & Fixes

### Issue: Deployment Fails

**Error**: "Repository not found"
**Fix**:
```bash
# Ensure GitHub repo is connected
gcloud builds repositories list
# Re-authenticate if needed
```

### Issue: 500 Internal Server Error

**Check**:
```bash
gcloud run services logs read press-release-collector --region=us-central1 --limit=50
```

**Common Causes**:
- Missing Bright Data credentials
- BigQuery permissions issue
- Invalid reference data

### Issue: Scheduler Jobs Don't Trigger

**Check**:
```bash
# Verify jobs exist
gcloud scheduler jobs list --location=us-central1

# Check specific job
gcloud scheduler jobs describe press-release-midnight --location=us-central1
```

**Fix**:
```bash
# Manually trigger to test
gcloud scheduler jobs run press-release-midnight --location=us-central1

# Check Cloud Run logs for errors
```

### Issue: Out of Memory

**Fix**:
```bash
gcloud run services update press-release-collector \
  --region=us-central1 \
  --memory=8Gi
```

### Issue: Timeout

**Fix**:
```bash
gcloud run services update press-release-collector \
  --region=us-central1 \
  --timeout=7200  # 2 hours
```

---

## 🔄 Updating the Deployment

### Update Code from GitHub

```bash
# Redeploy latest code
gcloud run deploy press-release-collector \
  --source=https://github.com/YOUR_USERNAME/press-release-collection \
  --region=us-central1
```

### Update Environment Variables

```bash
gcloud run services update press-release-collector \
  --region=us-central1 \
  --set-env-vars="NEW_VAR=value"
```

### Update Scheduler Date Range

```bash
gcloud scheduler jobs update http press-release-midnight \
  --location=us-central1 \
  --message-body='{"start_date": "2026-02-01", "end_date": "2026-02-15"}'
```

---

## 💰 Cost Monitoring

### Check Current Month Costs

Go to: [Cloud Console - Billing](https://console.cloud.google.com/billing)

Filter by:
- Service: Cloud Run
- Service: BigQuery
- Service: Cloud Scheduler

### Estimated Monthly Costs

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| Cloud Run | 3 runs/day × 30 days | $10-30 |
| BigQuery | Storage + queries | $5-15 |
| Cloud Scheduler | 3 jobs | $0.30 |
| **Total** | | **$15-45/month** |

**SERP API costs not included** (depends on Bright Data pricing)

---

## 📞 Support

### View Logs

```bash
# Stream logs
gcloud run services logs tail press-release-collector --region=us-central1

# View recent logs
gcloud run services logs read press-release-collector --region=us-central1 --limit=100
```

### Check Service Status

```bash
gcloud run services describe press-release-collector --region=us-central1
```

### List All Resources

```bash
# Cloud Run services
gcloud run services list --region=us-central1

# Scheduler jobs
gcloud scheduler jobs list --location=us-central1

# BigQuery datasets
bq ls

# Secrets
gcloud secrets list
```

---

## 🎯 Success Criteria

Your deployment is successful when:

- [x] Cloud Run service is deployed and shows "Ready"
- [x] Test request returns 200 OK
- [x] BigQuery tables are created
- [x] Scheduler jobs are created and enabled
- [x] First scheduled run completes successfully
- [x] Data appears in BigQuery tables
- [x] Idempotency works (re-running skips duplicates)
- [x] Backfill works (new companies get historical data)

---

## 📚 Additional Resources

- [DEPLOY_FROM_GITHUB.md](DEPLOY_FROM_GITHUB.md) - Detailed deployment guide
- [DEPLOYMENT.md](DEPLOYMENT.md) - Original deployment documentation
- [BIGQUERY_SCHEMA.md](BIGQUERY_SCHEMA.md) - Database schema reference
- [IDEMPOTENCY_GUIDE.md](IDEMPOTENCY_GUIDE.md) - Understanding deduplication
- [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) - Cost savings guide
- [CHANGELOG.md](CHANGELOG.md) - Recent changes

---

## 🔐 Security Notes

- ✅ Use Secret Manager for credentials (done by deploy scripts)
- ✅ Limit max instances to 1 (prevents race conditions)
- ⚠️  Current: `--allow-unauthenticated` (easy testing)
- 🔒 Production: Remove unauthenticated access, use service account only

To secure for production:
```bash
gcloud run services remove-iam-policy-binding press-release-collector \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"
```

This limits access to Cloud Scheduler only.
