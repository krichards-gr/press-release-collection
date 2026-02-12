# Fix Cloud Run Bright Data Secret
# This script updates the Bright Data proxy secret in Google Secret Manager
# with the correct format (no trailing newlines or whitespace)

$ErrorActionPreference = "Stop"

Write-Host "Fixing Bright Data Secret in Secret Manager" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

# Load proxy URL from .env file
$envFile = Get-Content .env
$proxyUrl = $null

foreach ($line in $envFile) {
    if ($line -match '^BRIGHT_DATA_PROXY_URL=(.+)$') {
        $proxyUrl = $matches[1].Trim()
        break
    }
}

if (-not $proxyUrl) {
    Write-Host "ERROR: Could not find BRIGHT_DATA_PROXY_URL in .env file" -ForegroundColor Red
    exit 1
}

Write-Host "Found proxy URL in .env file:" -ForegroundColor Yellow
Write-Host "  $proxyUrl" -ForegroundColor Gray
Write-Host ""

# Validate format
if (-not $proxyUrl.StartsWith('http://')) {
    Write-Host "ERROR: Proxy URL must start with 'http://'" -ForegroundColor Red
    exit 1
}

if (-not $proxyUrl.Contains('@')) {
    Write-Host "ERROR: Proxy URL must contain credentials (username:password@host)" -ForegroundColor Red
    exit 1
}

Write-Host "Validation passed. Updating secret in Google Secret Manager..." -ForegroundColor Green
Write-Host ""

# Update the secret (use Write-Output with -NoNewline to avoid adding newline)
$tempFile = New-TemporaryFile
[System.IO.File]::WriteAllText($tempFile.FullName, $proxyUrl, [System.Text.Encoding]::UTF8)

try {
    # Add new version to existing secret
    gcloud secrets versions add bright-data-proxy-url --data-file=$tempFile.FullName

    Write-Host ""
    Write-Host "✅ Secret updated successfully!" -ForegroundColor Green
    Write-Host ""

    # Verify the secret was updated
    Write-Host "Verifying secret..." -ForegroundColor Yellow
    $secretValue = gcloud secrets versions access latest --secret=bright-data-proxy-url 2>$null

    if ($secretValue -eq $proxyUrl) {
        Write-Host "✅ Secret verification passed!" -ForegroundColor Green
        Write-Host ""
        Write-Host "Secret value matches .env file (length: $($proxyUrl.Length) chars)" -ForegroundColor Gray
    } else {
        Write-Host "⚠️ Warning: Secret value doesn't match exactly" -ForegroundColor Yellow
        Write-Host "Expected length: $($proxyUrl.Length), Got: $($secretValue.Length)" -ForegroundColor Gray
    }

} catch {
    Write-Host "ERROR: Failed to update secret" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    Remove-Item $tempFile -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "==========" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Redeploy Cloud Run to pick up the new secret:" -ForegroundColor Yellow
Write-Host "   gcloud run deploy press-release-collector --region=us-central1 --source=." -ForegroundColor Gray
Write-Host ""
Write-Host "   (or trigger a new deployment from GitHub)" -ForegroundColor Gray
Write-Host ""
Write-Host "2. Test the deployment:" -ForegroundColor Yellow
Write-Host "   gcloud scheduler jobs run press-release-midnight --location=us-central1" -ForegroundColor Gray
Write-Host ""
Write-Host "3. Check logs for 'DEBUG - HTTP proxy' messages:" -ForegroundColor Yellow
Write-Host "   gcloud run services logs tail press-release-collector --region=us-central1" -ForegroundColor Gray
Write-Host ""
