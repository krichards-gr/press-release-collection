#!/bin/bash
# Quick Deployment Script for Press Release Collection Pipeline
# This script deploys to Cloud Run and sets up Cloud Scheduler

set -e  # Exit on error

echo "🚀 Press Release Collection Pipeline - Deployment Script"
echo "=========================================================="

# Configuration
read -p "Enter your GCP Project ID: " PROJECT_ID
read -p "Enter your GitHub repository URL (e.g., https://github.com/username/repo): " GITHUB_REPO
read -p "Enter your Bright Data Proxy URL: " BRIGHT_DATA_URL

REGION="us-central1"
SERVICE_NAME="press-release-collector"
DATASET="pressure_monitoring"

echo ""
echo "Configuration:"
echo "  Project ID: $PROJECT_ID"
echo "  Region: $REGION"
echo "  Service: $SERVICE_NAME"
echo "  GitHub: $GITHUB_REPO"
echo ""

read -p "Proceed with deployment? (y/n): " CONFIRM
if [[ $CONFIRM != "y" ]]; then
    echo "Deployment cancelled."
    exit 0
fi

echo ""
echo "📋 Step 1: Setting up GCP project..."
gcloud config set project $PROJECT_ID

echo ""
echo "📋 Step 2: Enabling required APIs..."
gcloud services enable run.googleapis.com \
    cloudbuild.googleapis.com \
    bigquery.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com

echo ""
echo "📋 Step 3: Creating BigQuery dataset..."
bq mk --dataset --location=US $DATASET 2>/dev/null || echo "Dataset already exists"

echo ""
echo "📋 Step 4: Storing Bright Data credentials in Secret Manager..."
echo -n "$BRIGHT_DATA_URL" | gcloud secrets create bright-data-proxy-url --data-file=- 2>/dev/null || \
    echo -n "$BRIGHT_DATA_URL" | gcloud secrets versions add bright-data-proxy-url --data-file=-

echo ""
echo "📋 Step 5: Deploying to Cloud Run from GitHub..."
gcloud run deploy $SERVICE_NAME \
    --source=$GITHUB_REPO \
    --region=$REGION \
    --memory=4Gi \
    --cpu=2 \
    --timeout=3600 \
    --max-instances=1 \
    --set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest \
    --set-env-vars="BIGQUERY_DATASET=$DATASET,GCP_PROJECT=$PROJECT_ID" \
    --allow-unauthenticated \
    --platform=managed

echo ""
echo "📋 Step 6: Getting service URL..."
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region=$REGION \
    --format='value(status.url)')
echo "Service URL: $SERVICE_URL"

echo ""
echo "📋 Step 7: Creating service account for Cloud Scheduler..."
gcloud iam service-accounts create cloud-scheduler-invoker \
    --display-name="Cloud Scheduler Invoker" 2>/dev/null || echo "Service account already exists"

gcloud run services add-iam-policy-binding $SERVICE_NAME \
    --region=$REGION \
    --member="serviceAccount:cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/run.invoker"

echo ""
echo "📋 Step 8: Creating Cloud Scheduler jobs..."

# Midnight EST (5 AM UTC)
echo "Creating midnight job..."
gcloud scheduler jobs create http press-release-midnight \
    --location=$REGION \
    --schedule="0 5 * * *" \
    --uri="$SERVICE_URL" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"start_date": "2026-02-01", "end_date": "2026-02-11"}' \
    --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" \
    --oidc-token-audience="$SERVICE_URL" 2>/dev/null || echo "Job already exists"

# Noon EST (5 PM UTC)
echo "Creating noon job..."
gcloud scheduler jobs create http press-release-noon \
    --location=$REGION \
    --schedule="0 17 * * *" \
    --uri="$SERVICE_URL" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"start_date": "2026-02-01", "end_date": "2026-02-11"}' \
    --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" \
    --oidc-token-audience="$SERVICE_URL" 2>/dev/null || echo "Job already exists"

# 4 PM EST (9 PM UTC)
echo "Creating 4pm job..."
gcloud scheduler jobs create http press-release-4pm \
    --location=$REGION \
    --schedule="0 21 * * *" \
    --uri="$SERVICE_URL" \
    --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"start_date": "2026-02-01", "end_date": "2026-02-11"}' \
    --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" \
    --oidc-token-audience="$SERVICE_URL" 2>/dev/null || echo "Job already exists"

echo ""
echo "✅ Deployment Complete!"
echo "======================="
echo ""
echo "Service URL: $SERVICE_URL"
echo ""
echo "Scheduler Jobs:"
echo "  - press-release-midnight (5 AM UTC / Midnight EST)"
echo "  - press-release-noon (5 PM UTC / Noon EST)"
echo "  - press-release-4pm (9 PM UTC / 4 PM EST)"
echo ""
echo "Next Steps:"
echo "1. Test the deployment:"
echo "   curl -X POST $SERVICE_URL -H 'Content-Type: application/json' -d '{\"start_date\": \"2026-02-10\", \"end_date\": \"2026-02-11\", \"skip_scraping\": true}'"
echo ""
echo "2. Test a scheduler job:"
echo "   gcloud scheduler jobs run press-release-midnight --location=$REGION"
echo ""
echo "3. View logs:"
echo "   gcloud run services logs tail $SERVICE_NAME --region=$REGION"
echo ""
echo "4. Check BigQuery:"
echo "   bq query 'SELECT * FROM $DATASET.collection_runs ORDER BY start_timestamp DESC LIMIT 10'"
echo ""
echo "📚 For more details, see DEPLOY_FROM_GITHUB.md"
