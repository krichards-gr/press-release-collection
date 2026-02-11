# Deployment Guide - Google Cloud Run

This guide covers deploying the press release collection pipeline to Google Cloud Run.

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
   ```

3. **Set Up BigQuery Dataset**
   ```bash
   bq mk --dataset --location=US pressure_monitoring
   ```

   Tables will be auto-created on first run. See [BIGQUERY_SCHEMA.md](BIGQUERY_SCHEMA.md) for schema details.

4. **Configure Environment Variables**

   Create a `.env.yaml` file (DO NOT COMMIT):
   ```yaml
   BRIGHT_DATA_PROXY_URL: "http://brd-customer-xxx-zone-xxx:password@brd.superproxy.io:33335"
   BIGQUERY_DATASET: "pressure_monitoring"
   GCP_PROJECT: "your-project-id"
   ```

## Deployment

### Option 1: Deploy with gcloud CLI

```bash
# Build and deploy in one command
gcloud run deploy press-release-collector \
  --source . \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --env-vars-file .env.yaml \
  --allow-unauthenticated
```

### Option 2: Deploy with Docker

```bash
# Build Docker image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/press-release-collector

# Deploy to Cloud Run
gcloud run deploy press-release-collector \
  --image gcr.io/YOUR_PROJECT_ID/press-release-collector \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --env-vars-file .env.yaml \
  --allow-unauthenticated
```

## Configuration

### Resource Limits

- **Memory**: 2Gi (increase if scraping large batches)
- **CPU**: 2 (increase for faster concurrent scraping)
- **Timeout**: 3600s (1 hour - adjust based on batch size)
- **Concurrency**: 1 (one request at a time)

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BRIGHT_DATA_PROXY_URL` | Bright Data proxy credentials | Yes |
| `BIGQUERY_DATASET` | BigQuery dataset name | No (default: pressure_monitoring) |
| `GCP_PROJECT` | Google Cloud project ID | No (auto-detected) |
| `MAX_SERP_PAGES` | Pages to collect per query | No (default: 10) |
| `SCRAPER_MAX_WORKERS` | Concurrent scraper threads | No (default: 10) |

## Testing

### Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run local server
python main.py

# Test with curl
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-01-01",
    "end_date": "2026-01-07"
  }'
```

### Cloud Testing

```bash
# Get service URL
SERVICE_URL=$(gcloud run services describe press-release-collector \
  --region us-central1 --format 'value(status.url)')

# Test endpoint
curl -X POST $SERVICE_URL \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2026-01-01",
    "end_date": "2026-01-07",
    "skip_scraping": true
  }'
```

## API Usage

### Request Format

```json
POST /
Content-Type: application/json

{
  "start_date": "2026-01-01",
  "end_date": "2026-01-31",
  "force_refresh": false,
  "skip_scraping": false
}
```

### Response Format

**Success (200)**:
```json
{
  "status": "success",
  "message": "Pipeline completed successfully. Processed 150 articles.",
  "run_id": "20260211_143022",
  "timestamp": "2026-02-11T14:35:45.123456",
  "stats": {
    "companies_count": 100,
    "queries_count": 100,
    "serp_results_count": 150,
    "articles_scraped": 135,
    "articles_enriched": 135
  }
}
```

**Error (400/500)**:
```json
{
  "status": "error",
  "message": "Invalid date format. Use YYYY-MM-DD",
  "run_id": "20260211_143022",
  "timestamp": "2026-02-11T14:30:22.123456",
  "stats": {}
}
```

## Scheduling

### Cloud Scheduler Setup

```bash
# Create a job to run daily at 2 AM
gcloud scheduler jobs create http daily-press-release-collection \
  --location us-central1 \
  --schedule "0 2 * * *" \
  --uri "$SERVICE_URL" \
  --http-method POST \
  --message-body '{
    "start_date": "$(date -d yesterday +%Y-%m-%d)",
    "end_date": "$(date +%Y-%m-%d)"
  }' \
  --headers "Content-Type=application/json"
```

### Weekly Full Refresh

```bash
# Weekly job on Sundays at 3 AM
gcloud scheduler jobs create http weekly-press-release-refresh \
  --location us-central1 \
  --schedule "0 3 * * 0" \
  --uri "$SERVICE_URL" \
  --http-method POST \
  --message-body '{
    "start_date": "$(date -d "7 days ago" +%Y-%m-%d)",
    "end_date": "$(date +%Y-%m-%d)",
    "force_refresh": true
  }' \
  --headers "Content-Type=application/json"
```

## Monitoring

### View Logs

```bash
# Stream logs
gcloud run services logs tail press-release-collector --region us-central1

# View recent logs
gcloud run services logs read press-release-collector \
  --region us-central1 \
  --limit 50
```

### Metrics

- **Request Count**: Number of pipeline runs
- **Request Duration**: Time per execution
- **Error Rate**: Failed pipeline runs
- **BigQuery**: Check table sizes and query costs

### BigQuery Monitoring

See [BIGQUERY_SCHEMA.md](BIGQUERY_SCHEMA.md) for detailed schema documentation and query examples.

```sql
-- Check recent collection runs
SELECT
  DATE(collection_timestamp) as date,
  COUNT(*) as articles_collected,
  COUNT(DISTINCT scraper_used) as scrapers_used
FROM `pressure_monitoring.collected_articles`
WHERE collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY date
ORDER BY date DESC;

-- Check enrichment coverage
SELECT
  COUNT(DISTINCT c.url) as total_articles,
  COUNT(DISTINCT e.url) as enriched_articles,
  ROUND(COUNT(DISTINCT e.url) / COUNT(DISTINCT c.url) * 100, 2) as coverage_pct
FROM `pressure_monitoring.collected_articles` c
LEFT JOIN `pressure_monitoring.article_enrichments` e ON c.url = e.url
WHERE c.collection_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
```

## Cost Optimization

1. **Reduce Memory**: Start with 2Gi, monitor, and adjust
2. **Minimize Timeout**: Set to realistic value (e.g., 1800s)
3. **Use Spot Instances**: Add `--execution-environment gen2`
4. **Schedule Off-Peak**: Run during low-cost hours
5. **Batch Processing**: Collect multiple days in one run

## Troubleshooting

### Issue: Timeout errors
**Solution**: Increase `--timeout` or reduce date range

### Issue: Out of memory
**Solution**: Increase `--memory` to 4Gi or 8Gi

### Issue: BigQuery permission denied
**Solution**: Ensure Cloud Run service account has BigQuery Data Editor role:
```bash
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT_EMAIL" \
  --role="roles/bigquery.dataEditor"
```

### Issue: Bright Data connection failed
**Solution**: Check `BRIGHT_DATA_PROXY_URL` environment variable

## Security

- ✅ Use Secret Manager for credentials (recommended)
- ✅ Enable authentication for production
- ✅ Use VPC for network isolation
- ✅ Enable Cloud Armor for DDoS protection
- ✅ Monitor access logs

## Updates

```bash
# Deploy new version
gcloud run deploy press-release-collector \
  --source . \
  --region us-central1

# Rollback to previous version
gcloud run services update-traffic press-release-collector \
  --region us-central1 \
  --to-revisions PREVIOUS_REVISION=100
```

## Support

For issues or questions:
1. Check Cloud Run logs
2. Verify BigQuery table schema
3. Test locally first
4. Review error traces in response JSON
