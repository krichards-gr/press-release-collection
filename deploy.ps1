# Quick Deployment Script for Press Release Collection Pipeline (PowerShell)
# This script deploys to Cloud Run and sets up Cloud Scheduler

$ErrorActionPreference = "Stop"

Write-Host "🚀 Press Release Collection Pipeline - Deployment Script" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Configuration
$PROJECT_ID = Read-Host "Enter your GCP Project ID"
$GITHUB_REPO = Read-Host "Enter your GitHub repository URL (e.g., https://github.com/username/repo)"
$BRIGHT_DATA_URL = Read-Host "Enter your Bright Data Proxy URL"

$REGION = "us-central1"
$SERVICE_NAME = "press-release-collector"
$DATASET = "pressure_monitoring"

Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Project ID: $PROJECT_ID"
Write-Host "  Region: $REGION"
Write-Host "  Service: $SERVICE_NAME"
Write-Host "  GitHub: $GITHUB_REPO"
Write-Host ""

$CONFIRM = Read-Host "Proceed with deployment? (y/n)"
if ($CONFIRM -ne "y") {
    Write-Host "Deployment cancelled." -ForegroundColor Red
    exit 0
}

Write-Host ""
Write-Host "📋 Step 1: Setting up GCP project..." -ForegroundColor Green
gcloud config set project $PROJECT_ID

Write-Host ""
Write-Host "📋 Step 2: Enabling required APIs..." -ForegroundColor Green
gcloud services enable run.googleapis.com cloudbuild.googleapis.com bigquery.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com

Write-Host ""
Write-Host "📋 Step 3: Creating BigQuery dataset..." -ForegroundColor Green
try {
    bq mk --dataset --location=US $DATASET 2>$null
} catch {
    Write-Host "Dataset already exists" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "📋 Step 4: Storing Bright Data credentials in Secret Manager..." -ForegroundColor Green
try {
    $BRIGHT_DATA_URL | gcloud secrets create bright-data-proxy-url --data-file=- 2>$null
} catch {
    $BRIGHT_DATA_URL | gcloud secrets versions add bright-data-proxy-url --data-file=-
}

Write-Host ""
Write-Host "📋 Step 5: Deploying to Cloud Run from GitHub..." -ForegroundColor Green
Write-Host "This may take 5-10 minutes for first deployment..." -ForegroundColor Yellow
gcloud run deploy $SERVICE_NAME `
    --source=$GITHUB_REPO `
    --region=$REGION `
    --memory=4Gi `
    --cpu=2 `
    --timeout=3600 `
    --max-instances=1 `
    --set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest `
    --set-env-vars="BIGQUERY_DATASET=$DATASET,GCP_PROJECT=$PROJECT_ID" `
    --allow-unauthenticated `
    --platform=managed

Write-Host ""
Write-Host "📋 Step 6: Getting service URL..." -ForegroundColor Green
$SERVICE_URL = gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)'
Write-Host "Service URL: $SERVICE_URL" -ForegroundColor Cyan

Write-Host ""
Write-Host "📋 Step 7: Creating service account for Cloud Scheduler..." -ForegroundColor Green
try {
    gcloud iam service-accounts create cloud-scheduler-invoker --display-name="Cloud Scheduler Invoker" 2>$null
} catch {
    Write-Host "Service account already exists" -ForegroundColor Yellow
}

gcloud run services add-iam-policy-binding $SERVICE_NAME `
    --region=$REGION `
    --member="serviceAccount:cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" `
    --role="roles/run.invoker"

Write-Host ""
Write-Host "📋 Step 8: Creating Cloud Scheduler jobs..." -ForegroundColor Green

# Get date range for scheduler jobs (rolling 10-day window)
$END_DATE = Get-Date -Format "yyyy-MM-dd"
$START_DATE = (Get-Date).AddDays(-10).ToString("yyyy-MM-dd")

Write-Host "Using date range: $START_DATE to $END_DATE" -ForegroundColor Yellow

# Midnight EST (5 AM UTC)
Write-Host "Creating midnight job..."
try {
    gcloud scheduler jobs create http press-release-midnight `
        --location=$REGION `
        --schedule="0 5 * * *" `
        --uri="$SERVICE_URL" `
        --http-method=POST `
        --headers="Content-Type=application/json" `
        --message-body="{`"start_date`": `"$START_DATE`", `"end_date`": `"$END_DATE`"}" `
        --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" `
        --oidc-token-audience="$SERVICE_URL" 2>$null
} catch {
    Write-Host "Job already exists" -ForegroundColor Yellow
}

# Noon EST (5 PM UTC)
Write-Host "Creating noon job..."
try {
    gcloud scheduler jobs create http press-release-noon `
        --location=$REGION `
        --schedule="0 17 * * *" `
        --uri="$SERVICE_URL" `
        --http-method=POST `
        --headers="Content-Type=application/json" `
        --message-body="{`"start_date`": `"$START_DATE`", `"end_date`": `"$END_DATE`"}" `
        --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" `
        --oidc-token-audience="$SERVICE_URL" 2>$null
} catch {
    Write-Host "Job already exists" -ForegroundColor Yellow
}

# 4 PM EST (9 PM UTC)
Write-Host "Creating 4pm job..."
try {
    gcloud scheduler jobs create http press-release-4pm `
        --location=$REGION `
        --schedule="0 21 * * *" `
        --uri="$SERVICE_URL" `
        --http-method=POST `
        --headers="Content-Type=application/json" `
        --message-body="{`"start_date`": `"$START_DATE`", `"end_date`": `"$END_DATE`"}" `
        --oidc-service-account-email="cloud-scheduler-invoker@$PROJECT_ID.iam.gserviceaccount.com" `
        --oidc-token-audience="$SERVICE_URL" 2>$null
} catch {
    Write-Host "Job already exists" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "✅ Deployment Complete!" -ForegroundColor Green
Write-Host "=======================" -ForegroundColor Green
Write-Host ""
Write-Host "Service URL: $SERVICE_URL" -ForegroundColor Cyan
Write-Host ""
Write-Host "Scheduler Jobs:" -ForegroundColor Yellow
Write-Host "  - press-release-midnight (5 AM UTC / Midnight EST)"
Write-Host "  - press-release-noon (5 PM UTC / Noon EST)"
Write-Host "  - press-release-4pm (9 PM UTC / 4 PM EST)"
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "1. Test the deployment:"
Write-Host "   curl -X POST $SERVICE_URL -H 'Content-Type: application/json' -d '{`"start_date`": `"2026-02-10`", `"end_date`": `"2026-02-11`", `"skip_scraping`": true}'"
Write-Host ""
Write-Host "2. Test a scheduler job:"
Write-Host "   gcloud scheduler jobs run press-release-midnight --location=$REGION"
Write-Host ""
Write-Host "3. View logs:"
Write-Host "   gcloud run services logs tail $SERVICE_NAME --region=$REGION"
Write-Host ""
Write-Host "4. Check BigQuery:"
Write-Host "   bq query 'SELECT * FROM $DATASET.collection_runs ORDER BY start_timestamp DESC LIMIT 10'"
Write-Host ""
Write-Host "📚 For more details, see DEPLOY_FROM_GITHUB.md" -ForegroundColor Cyan
