# Deploy to Google Cloud Run from GitHub

This guide walks through deploying the press release collection pipeline to Google Cloud Run from a GitHub repository and setting up Cloud Scheduler for automated runs.

---

## Prerequisites

1. **Google Cloud Project**
   ```bash
   gcloud config set project YOUR_PROJECT_ID
   ```

2. **Enable Required APIs**
   ```bash
   gcloud services enable run.googleapis.com
   gcloud services enable cloudbuild.googleapis.com
   gcloud services enable bigquery.googleapis.com
   gcloud services enable cloudscheduler.googleapis.com
   gcloud services enable secretmanager.googleapis.com
   ```

3. **GitHub Repository**
   - Push your code to GitHub
   - Note your repository URL: `https://github.com/YOUR_USERNAME/press-release-collection`

4. **Bright Data Credentials**
   - Get your Bright Data proxy URL
   - Format: `http://brd-customer-XXX-zone-XXX:PASSWORD@brd.superproxy.io:33335`

---

## Step 1: Set Up BigQuery Dataset

```bash
# Create dataset
bq mk --dataset --location=US pressure_monitoring

# Verify creation
bq ls
```

Tables will be auto-created on first run.

---

## Step 2: Store Secrets in Secret Manager

Instead of plain environment variables, use Secret Manager for credentials:

```bash
# Store Bright Data credentials
echo -n "http://brd-customer-XXX-zone-XXX:PASSWORD@brd.superproxy.io:33335" | \
  gcloud secrets create bright-data-proxy-url --data-file=-

# Verify
gcloud secrets describe bright-data-proxy-url
```

---

## Step 3: Connect GitHub Repository to Cloud Build

### Option A: Via Console (Recommended for First-Time Setup)

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers)
2. Click "Connect Repository"
3. Select "GitHub" → Authenticate → Select your repository
4. Click "Connect" (don't create trigger yet, we'll deploy manually)

### Option B: Via gcloud CLI

```bash
# This requires GitHub App installation
# Follow prompts to authenticate with GitHub
gcloud alpha builds repositories create press-release-collection \
  --remote-uri=https://github.com/YOUR_USERNAME/press-release-collection.git \
  --connection=YOUR_CONNECTION_NAME
```

---

## Step 4: Deploy to Cloud Run from GitHub

### Get Your Project Number
```bash
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format="value(projectNumber)")
echo "Project Number: $PROJECT_NUMBER"
```

### Deploy from GitHub Repository

```bash
gcloud run deploy press-release-collector \
  --source=https://github.com/YOUR_USERNAME/press-release-collection \
  --region=us-central1 \
  --memory=4Gi \
  --cpu=2 \
  --timeout=3600 \
  --max-instances=1 \
  --set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest \
  --set-env-vars="BIGQUERY_DATASET=pressure_monitoring,GCP_PROJECT=$(gcloud config get-value project)" \
  --allow-unauthenticated \
  --platform=managed
```

**Parameters Explained**:
- `--source`: GitHub repository URL
- `--memory=4Gi`: Enough for scraping large batches
- `--cpu=2`: Parallel processing
- `--timeout=3600`: 1 hour max (adjust if needed)
- `--max-instances=1`: Prevents concurrent runs (important for deduplication)
- `--set-secrets`: Securely inject Bright Data credentials
- `--allow-unauthenticated`: Allow Cloud Scheduler to invoke (consider auth for production)

**Note**: First deployment may take 5-10 minutes as it builds the Docker image.

---

## Step 5: Get Service URL

```bash
SERVICE_URL=$(gcloud run services describe press-release-collector \
  --region=us-central1 \
  --format='value(status.url)')

echo "Service URL: $SERVICE_URL"
```

---

## Step 6: Test Deployment

### Test with Skip Scraping (Fast)
```bash
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-01",
    "end_date": "2026-02-02",
    "skip_scraping": true
  }'
```

Expected response:
```json
{
  "status": "success",
  "message": "Pipeline completed successfully...",
  "run_id": "20260211_143022",
  "stats": {
    "queries_generated": 100,
    "queries_executed": 100,
    "serp_results_count": 50
  }
}
```

### Test Full Pipeline (Slower)
```bash
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-02-01",
    "end_date": "2026-02-02"
  }'
```

---

## Step 7: Set Up Cloud Scheduler

Cloud Scheduler will trigger the pipeline 3 times daily:
- **Midnight EST** (5 AM UTC)
- **Noon EST** (5 PM UTC)
- **4 PM EST** (9 PM UTC)

### Create Service Account for Scheduler

```bash
# Create service account
gcloud iam service-accounts create cloud-scheduler-invoker \
  --display-name="Cloud Scheduler Invoker"

# Grant permission to invoke Cloud Run
gcloud run services add-iam-policy-binding press-release-collector \
  --region=us-central1 \
  --member="serviceAccount:cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### Create Scheduler Jobs

**Note**: Cloud Scheduler uses UTC, so EST times are converted:
- Midnight EST = 5 AM UTC (or 6 AM during DST)
- Noon EST = 5 PM UTC (or 6 PM during DST)
- 4 PM EST = 9 PM UTC (or 10 PM during DST)

Using **standard time (non-DST)**:

#### Job 1: Midnight EST (5 AM UTC)
```bash
gcloud scheduler jobs create http press-release-midnight \
  --location=us-central1 \
  --schedule="0 5 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{
    "start_date": "'"$(date -u -d 'yesterday' +%Y-%m-%d)"'",
    "end_date": "'"$(date -u +%Y-%m-%d)"'"
  }' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"
```

#### Job 2: Noon EST (5 PM UTC)
```bash
gcloud scheduler jobs create http press-release-noon \
  --location=us-central1 \
  --schedule="0 17 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{
    "start_date": "'"$(date -u -d 'yesterday' +%Y-%m-%d)"'",
    "end_date": "'"$(date -u +%Y-%m-%d)"'"
  }' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"
```

#### Job 3: 4 PM EST (9 PM UTC)
```bash
gcloud scheduler jobs create http press-release-4pm \
  --location=us-central1 \
  --schedule="0 21 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{
    "start_date": "'"$(date -u -d 'yesterday' +%Y-%m-%d)"'",
    "end_date": "'"$(date -u +%Y-%m-%d)"'"
  }' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"
```

**Note**: The date logic above uses bash date commands. For Windows/PowerShell, you may need to manually set dates or use a simpler approach (see Alternative below).

### Alternative: Simpler Scheduler Jobs (Static Date Range)

If dynamic dates are complex, use a rolling 2-day window:

```bash
# Midnight EST
gcloud scheduler jobs create http press-release-midnight \
  --location=us-central1 \
  --schedule="0 5 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"start_date": "2026-02-10", "end_date": "2026-02-11"}' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"

# Noon EST
gcloud scheduler jobs create http press-release-noon \
  --location=us-central1 \
  --schedule="0 17 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"start_date": "2026-02-10", "end_date": "2026-02-11"}' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"

# 4 PM EST
gcloud scheduler jobs create http press-release-4pm \
  --location=us-central1 \
  --schedule="0 21 * * *" \
  --uri="$SERVICE_URL" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"start_date": "2026-02-10", "end_date": "2026-02-11"}' \
  --oidc-service-account-email="cloud-scheduler-invoker@$(gcloud config get-value project).iam.gserviceaccount.com" \
  --oidc-token-audience="$SERVICE_URL"
```

**Recommendation**: Use a rolling 7-day window to ensure coverage:
```json
{"start_date": "2026-02-04", "end_date": "2026-02-11"}
```

Then update the dates weekly or use a Cloud Function to generate dynamic dates.

---

## Step 8: Verify Scheduler Jobs

```bash
# List jobs
gcloud scheduler jobs list --location=us-central1

# Test a job manually (don't wait for schedule)
gcloud scheduler jobs run press-release-midnight --location=us-central1

# Check job logs
gcloud scheduler jobs describe press-release-midnight --location=us-central1
```

---

## Step 9: Monitor Execution

### View Cloud Run Logs
```bash
# Stream logs
gcloud run services logs tail press-release-collector --region=us-central1

# View recent logs
gcloud run services logs read press-release-collector \
  --region=us-central1 \
  --limit=100
```

### Check BigQuery
```sql
-- Recent runs
SELECT *
FROM `pressure_monitoring.collection_runs`
ORDER BY start_timestamp DESC
LIMIT 10;

-- Today's collections
SELECT COUNT(*) as articles_collected
FROM `pressure_monitoring.collected_articles`
WHERE DATE(collection_timestamp) = CURRENT_DATE();
```

### Cloud Scheduler Logs
```bash
# View scheduler execution logs
gcloud logging read "resource.type=cloud_scheduler_job" \
  --limit=50 \
  --format=json
```

---

## Updating the Deployment

### Redeploy from GitHub

When you push changes to GitHub:

```bash
# Redeploy (uses latest code from GitHub)
gcloud run deploy press-release-collector \
  --source=https://github.com/YOUR_USERNAME/press-release-collection \
  --region=us-central1
```

**Note**: Cloud Build caches layers, so rebuilds are faster (~2-3 minutes).

### Continuous Deployment (Optional)

Set up automatic deployments on push to main:

1. Go to [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers)
2. Click "Create Trigger"
3. Configure:
   - **Name**: `deploy-press-release-collector`
   - **Event**: Push to branch
   - **Branch**: `^main$`
   - **Build Configuration**: Cloud Build configuration file
4. Create `cloudbuild.yaml` in your repo:

```yaml
steps:
  # Build and deploy to Cloud Run
  - name: 'gcr.io/google.com/cloudsdktool/cloud-sdk'
    entrypoint: gcloud
    args:
      - 'run'
      - 'deploy'
      - 'press-release-collector'
      - '--source=.'
      - '--region=us-central1'
      - '--memory=4Gi'
      - '--cpu=2'
      - '--timeout=3600'
      - '--max-instances=1'
      - '--set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest'
      - '--set-env-vars=BIGQUERY_DATASET=pressure_monitoring'
      - '--allow-unauthenticated'
      - '--platform=managed'

timeout: 1200s
```

---

## Cost Optimization

### Minimize Costs

1. **Max Instances**: Set to 1 (prevents concurrent runs)
   ```bash
   --max-instances=1
   ```

2. **Min Instances**: Keep at 0 (scale to zero when idle)
   ```bash
   --min-instances=0
   ```

3. **Memory**: Start with 4Gi, monitor and adjust
   ```bash
   --memory=4Gi
   ```

4. **CPU**: Use 2 for parallel scraping
   ```bash
   --cpu=2
   ```

5. **Timeout**: Set realistically (1 hour = 3600s)
   ```bash
   --timeout=3600
   ```

### Cost Estimates

- **Cloud Run**: ~$0.10-0.50 per execution (depends on duration)
- **BigQuery**: ~$5-10/month storage + query costs
- **Cloud Scheduler**: $0.10/month per job ($0.30 total for 3 jobs)
- **SERP API**: Depends on Bright Data pricing

**Total Estimate**: $20-50/month for 3 daily runs

---

## Troubleshooting

### Issue: Deployment Fails

**Check**:
```bash
# View build logs
gcloud builds list --limit=5

# Get specific build details
gcloud builds describe BUILD_ID
```

**Common Fixes**:
- Ensure all dependencies in `pyproject.toml`
- Check Dockerfile syntax
- Verify GitHub connection

### Issue: Service Returns 500 Error

**Check**:
```bash
# View logs
gcloud run services logs read press-release-collector --region=us-central1 --limit=50
```

**Common Fixes**:
- Verify BRIGHT_DATA_PROXY_URL secret is set
- Check BigQuery permissions
- Ensure dataset exists

### Issue: Scheduler Jobs Don't Run

**Check**:
```bash
# Verify job configuration
gcloud scheduler jobs describe press-release-midnight --location=us-central1

# Check IAM permissions
gcloud run services get-iam-policy press-release-collector --region=us-central1
```

**Common Fixes**:
- Verify service account has `roles/run.invoker`
- Check OIDC token audience matches service URL
- Ensure scheduler location matches Cloud Run region

### Issue: Timeout Errors

**Solution**:
```bash
# Increase timeout
gcloud run services update press-release-collector \
  --region=us-central1 \
  --timeout=7200  # 2 hours
```

### Issue: Out of Memory

**Solution**:
```bash
# Increase memory
gcloud run services update press-release-collector \
  --region=us-central1 \
  --memory=8Gi
```

---

## Security Best Practices

### 1. Use Authenticated Requests (Recommended for Production)

```bash
# Remove unauthenticated access
gcloud run services remove-iam-policy-binding press-release-collector \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"

# Only scheduler can invoke
# (Already configured via service account)
```

### 2. Use VPC Connector (Optional)

For network isolation:
```bash
gcloud compute networks vpc-access connectors create press-release-connector \
  --region=us-central1 \
  --network=default

gcloud run services update press-release-collector \
  --region=us-central1 \
  --vpc-connector=press-release-connector
```

### 3. Use Secret Manager (Already Configured)

Never hardcode credentials - use Secret Manager as shown in Step 2.

### 4. Enable Cloud Armor (Optional)

For DDoS protection if service is public.

---

## Maintenance

### Update Environment Variables

```bash
gcloud run services update press-release-collector \
  --region=us-central1 \
  --set-env-vars="NEW_VAR=value"
```

### Update Secrets

```bash
# Update Bright Data credentials
echo -n "NEW_PROXY_URL" | gcloud secrets versions add bright-data-proxy-url --data-file=-
```

### Update Scheduler Schedule

```bash
# Pause job
gcloud scheduler jobs pause press-release-midnight --location=us-central1

# Update schedule
gcloud scheduler jobs update http press-release-midnight \
  --location=us-central1 \
  --schedule="0 6 * * *"  # New time

# Resume job
gcloud scheduler jobs resume press-release-midnight --location=us-central1
```

### Delete Resources

```bash
# Delete Cloud Run service
gcloud run services delete press-release-collector --region=us-central1

# Delete scheduler jobs
gcloud scheduler jobs delete press-release-midnight --location=us-central1
gcloud scheduler jobs delete press-release-noon --location=us-central1
gcloud scheduler jobs delete press-release-4pm --location=us-central1

# Delete BigQuery dataset (⚠️ WARNING: Deletes all data)
bq rm -r -f -d pressure_monitoring
```

---

## Summary

You now have:
- ✅ Cloud Run service deployed from GitHub
- ✅ Automated runs 3x daily (midnight, noon, 4pm EST)
- ✅ Secure credentials via Secret Manager
- ✅ Idempotency preventing duplicate costs
- ✅ BigQuery storage with run tracking
- ✅ Automatic backfill for new URLs

**Next Steps**:
1. Monitor first few runs
2. Adjust memory/CPU as needed
3. Set up alerting for failures
4. Configure continuous deployment (optional)
