# Quick Start: Deploy to Cloud Run

Get your press release collection pipeline running in ~15 minutes.

---

## Before You Start

Gather these items:

1. **Google Cloud Project ID**
   - Create at: https://console.cloud.google.com/projectcreate
   - Enable billing

2. **GitHub Repository URL**
   - Push your code to GitHub first
   - Example: `https://github.com/username/press-release-collection`

3. **Bright Data Proxy URL**
   - Format: `http://brd-customer-XXX-zone-XXX:PASSWORD@brd.superproxy.io:33335`

4. **gcloud CLI Installed**
   - Download: https://cloud.google.com/sdk/docs/install
   - Authenticate: `gcloud auth login`

---

## Step 1: Push Code to GitHub

If you haven't already:

```bash
cd C:\Users\KRosh\To_Backup\Tools\press-release-collection

# Initialize git (if needed)
git init
git add .
git commit -m "Initial commit"

# Add remote and push
git remote add origin https://github.com/YOUR_USERNAME/press-release-collection.git
git branch -M main
git push -u origin main
```

---

## Step 2: Run Deployment Script

### Windows (PowerShell)

```powershell
cd C:\Users\KRosh\To_Backup\Tools\press-release-collection
.\deploy.ps1
```

### Mac/Linux (Bash)

```bash
cd /path/to/press-release-collection
chmod +x deploy.sh
./deploy.sh
```

**The script will prompt you for**:
1. GCP Project ID
2. GitHub repository URL
3. Bright Data proxy URL

**Then automatically**:
- Enables required Google Cloud APIs
- Creates BigQuery dataset (`pressure_monitoring`)
- Stores credentials in Secret Manager
- Deploys to Cloud Run from GitHub (~10 min)
- Sets up 3 Cloud Scheduler jobs

---

## Step 3: Test Deployment

After deployment completes, you'll see the service URL. Test it:

```bash
# Replace with your actual service URL
SERVICE_URL="https://press-release-collector-XXX.a.run.app"

# Quick test (no scraping, ~30 seconds)
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-10",
    "end_date": "2026-02-11",
    "skip_scraping": true
  }'
```

**Expected Response**:
```json
{
  "status": "success",
  "message": "Pipeline completed successfully. Processed 50 articles.",
  "run_id": "20260211_143022",
  "stats": {
    "queries_generated": 100,
    "queries_executed": 100,
    "serp_results_count": 50
  }
}
```

✅ **Success!** Your pipeline is deployed and working.

---

## Step 4: Test Full Pipeline

Test with actual scraping (~5-15 minutes):

```bash
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-10",
    "end_date": "2026-02-11"
  }'
```

**Expected Response**:
```json
{
  "status": "success",
  "stats": {
    "queries_executed": 100,
    "serp_results_count": 50,
    "articles_scraped": 45
  }
}
```

---

## Step 5: Test Scheduler

Manually trigger a scheduled job:

```bash
gcloud scheduler jobs run press-release-midnight --location=us-central1
```

Check logs:
```bash
gcloud run services logs tail press-release-collector --region=us-central1
```

---

## Step 6: Verify BigQuery

```bash
# Check collection runs
bq query 'SELECT * FROM pressure_monitoring.collection_runs ORDER BY start_timestamp DESC LIMIT 5'

# Check collected articles
bq query 'SELECT COUNT(*) as total FROM pressure_monitoring.collected_articles'

# Check enrichments
bq query 'SELECT sentiment, COUNT(*) as count FROM pressure_monitoring.article_enrichments GROUP BY sentiment'
```

---

## Your Scheduler Jobs

The deployment created 3 scheduled jobs:

| Job | Time (EST) | Time (UTC) | Frequency |
|-----|------------|------------|-----------|
| `press-release-midnight` | Midnight | 5:00 AM | Daily |
| `press-release-noon` | Noon | 5:00 PM | Daily |
| `press-release-4pm` | 4:00 PM | 9:00 PM | Daily |

**How it works**:
1. Jobs run automatically at scheduled times
2. Each job collects press releases from the past 10 days
3. Idempotency ensures no duplicates (re-running is free!)
4. New companies automatically backfill from 2026-01-01

---

## Monitoring Your Pipeline

### View Service Logs
```bash
gcloud run services logs tail press-release-collector --region=us-central1
```

### Check Scheduler Jobs
```bash
gcloud scheduler jobs list --location=us-central1
```

### BigQuery Dashboard

Create this saved query in BigQuery:
```sql
-- Daily Collection Summary
SELECT
  DATE(start_timestamp) as date,
  COUNT(*) as runs,
  SUM(queries_executed) as queries,
  SUM(urls_collected) as urls,
  SUM(articles_scraped) as articles,
  COUNTIF(status = 'failed') as failures
FROM `pressure_monitoring.collection_runs`
WHERE start_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY date
ORDER BY date DESC;
```

---

## Cost Monitoring

### View Current Costs

Go to: https://console.cloud.google.com/billing

Filter by these services:
- Cloud Run
- BigQuery
- Cloud Scheduler

### Expected Monthly Costs

| Component | Estimated Cost |
|-----------|---------------|
| Cloud Run (90 runs/month) | $10-30 |
| BigQuery (storage + queries) | $5-15 |
| Cloud Scheduler (3 jobs) | $0.30 |
| **Total** | **$15-45/month** |

*SERP API costs depend on Bright Data pricing*

**Cost Savings from Idempotency**:
- Re-runs skip already-executed queries = $0 SERP cost
- New companies backfill automatically without re-querying existing companies
- Typical savings: 70-90% on SERP API costs

---

## Updating Your Pipeline

### Update Code

1. Make changes to your code locally
2. Commit and push to GitHub:
   ```bash
   git add .
   git commit -m "Update description"
   git push
   ```

3. Redeploy from GitHub:
   ```bash
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
# Update to collect last 7 days instead of 10
gcloud scheduler jobs update http press-release-midnight \
  --location=us-central1 \
  --message-body='{"start_date": "2026-02-04", "end_date": "2026-02-11"}'
```

---

## Troubleshooting

### Deployment Failed

**Error**: "Repository not found"
**Fix**: Ensure you pushed code to GitHub and used correct URL

**Error**: "API not enabled"
**Fix**: Run the script again, it enables APIs automatically

### Service Returns 500 Error

**Check logs**:
```bash
gcloud run services logs read press-release-collector --region=us-central1 --limit=50
```

**Common causes**:
- Missing Bright Data credentials
- Invalid reference data CSV
- BigQuery permissions

### Scheduler Jobs Don't Run

**Test manually**:
```bash
gcloud scheduler jobs run press-release-midnight --location=us-central1
```

**Check permissions**:
```bash
gcloud run services get-iam-policy press-release-collector --region=us-central1
```

Should show: `cloud-scheduler-invoker@PROJECT.iam.gserviceaccount.com` with `roles/run.invoker`

---

## Next Steps

1. ✅ Monitor first few scheduled runs
2. ✅ Verify data is flowing into BigQuery
3. ✅ Set up alerting for failures (optional)
4. ✅ Create BigQuery views/dashboards
5. ✅ Add more companies to reference data (auto-backfills!)

---

## Resources

- **[DEPLOY_FROM_GITHUB.md](DEPLOY_FROM_GITHUB.md)** - Detailed deployment guide
- **[DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)** - Testing checklist
- **[IDEMPOTENCY_GUIDE.md](IDEMPOTENCY_GUIDE.md)** - Understanding deduplication
- **[COST_OPTIMIZATION.md](COST_OPTIMIZATION.md)** - Cost savings explained
- **[BIGQUERY_SCHEMA.md](BIGQUERY_SCHEMA.md)** - Database schema

---

## Support

**Issues**:
- Check logs: `gcloud run services logs tail press-release-collector --region=us-central1`
- View BigQuery: https://console.cloud.google.com/bigquery
- Cloud Console: https://console.cloud.google.com/run

**Common Questions**:

**Q: Can I change the schedule?**
A: Yes! Update scheduler jobs with `gcloud scheduler jobs update http JOB_NAME --schedule="NEW_CRON"`

**Q: How do I add new companies?**
A: Add them to your reference data CSV and push to GitHub. Next run will automatically backfill from 2026-01-01.

**Q: What if I want to collect more historical data?**
A: Update the scheduler jobs to use an earlier start_date, or manually trigger:
```bash
curl -X POST $SERVICE_URL -d '{"start_date": "2025-01-01", "end_date": "2026-01-01"}'
```

**Q: How do I stop the scheduled runs?**
A: Pause scheduler jobs:
```bash
gcloud scheduler jobs pause press-release-midnight --location=us-central1
gcloud scheduler jobs pause press-release-noon --location=us-central1
gcloud scheduler jobs pause press-release-4pm --location=us-central1
```

**Q: Can I run it more/less frequently?**
A: Yes! Create/delete scheduler jobs or update their schedules as needed.

---

## Success! 🎉

Your press release collection pipeline is now:
- ✅ Deployed to Google Cloud Run
- ✅ Running automatically 3x daily
- ✅ Storing data in BigQuery
- ✅ Deduplicating to save costs
- ✅ Auto-backfilling new companies

**Happy collecting!** 📰
