# Troubleshooting Guide

## Common Issues and Solutions

### Issue: SERP Collection Failing with Proxy Errors

**Symptoms:**
```
⚠️ Request failed: HTTPSConnectionPool(host='www.google.com', port=443): Max retries exceeded
```

**Root Cause:**
The Bright Data proxy configuration in Cloud Run Secret Manager had formatting issues (trailing whitespace, newlines, or missing protocol prefix).

**Solution:**

1. **Verify Local Configuration:**
   ```bash
   python verify_proxy.py
   ```

2. **Fix Cloud Run Secret:**
   ```bash
   # Update the secret with correct format (no trailing newline)
   echo -n "http://brd-customer-xxx-zone-xxx:password@brd.superproxy.io:33335" | \
   gcloud secrets versions add bright-data-proxy-url --data-file=-
   ```

3. **Redeploy Cloud Run:**
   ```bash
   gcloud run deploy press-release-collector \
     --region=us-central1 \
     --source=https://github.com/your-repo/press-release-collection \
     --set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest \
     --set-env-vars="BIGQUERY_DATASET=pressure_monitoring" \
     --memory=4Gi \
     --cpu=2 \
     --timeout=3600 \
     --max-instances=1 \
     --allow-unauthenticated
   ```

4. **Verify Fix:**
   Check logs for debug messages:
   ```bash
   gcloud run services logs tail press-release-collector --region=us-central1
   ```

   Look for:
   ```
   DEBUG - HTTP proxy: http://brd-customer-xxx:***@brd.superproxy.io:33335
   DEBUG - Proxy format check: HTTP starts with 'http://'? True
   ```

---

### Issue: Malformed Search Queries

**Symptoms:**
```
⚠️ Request failed: .../search?q=site:
```

**Root Cause:**
Empty or NaN values in the `newsroom_url` column of the reference data were creating malformed queries.

**Solution:**
The code now validates and skips invalid URLs:
- Filters out NaN values using `dropna()`
- Skips empty or whitespace-only URLs
- Logs the number of valid URLs and generated queries

**Verification:**
Look for these log messages:
```
📝 Loaded 107 valid newsroom URLs from reference data
✅ Generated 107 search queries
```

---

### Issue: Secret Manager vs Environment Variables

**Understanding the Configuration:**

The pipeline supports multiple ways to set proxy URLs:

1. **Single URL (recommended):**
   ```bash
   BRIGHT_DATA_PROXY_URL=http://user:pass@host:port
   ```
   The code automatically uses this for both HTTP and HTTPS.

2. **Separate URLs (advanced):**
   ```bash
   BRIGHT_DATA_PROXY_URL_HTTP=http://user:pass@host:port
   BRIGHT_DATA_PROXY_URL_HTTPS=http://user:pass@host:port
   ```

**For Cloud Run:**
- Proxy credentials are stored in Secret Manager (not environment variables)
- The secret is mounted as `BRIGHT_DATA_PROXY_URL`
- Deployment command: `--set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest`

**Important:**
- The proxy URL for HTTPS requests should still start with `http://` (not `https://`)
- This is standard HTTP CONNECT tunneling behavior
- The proxy handles the HTTPS connection to Google

---

### Issue: Checking Current Secret Value

**View the secret:**
```bash
gcloud secrets versions access latest --secret=bright-data-proxy-url
```

**View secret metadata:**
```bash
gcloud secrets describe bright-data-proxy-url
```

**List all versions:**
```bash
gcloud secrets versions list bright-data-proxy-url
```

---

### Issue: Cloud Run Not Picking Up New Secret Version

**Root Cause:**
Cloud Run caches secrets and may not pick up new versions immediately.

**Solution:**
Force a redeployment:
```bash
gcloud run deploy press-release-collector \
  --region=us-central1 \
  --source=. \
  --update-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:latest
```

Or specify a specific version:
```bash
--set-secrets=BRIGHT_DATA_PROXY_URL=bright-data-proxy-url:7
```

---

### Debugging Checklist

When troubleshooting SERP collection issues:

- [ ] Run `python verify_proxy.py` locally to check configuration
- [ ] Verify secret in Secret Manager has no trailing whitespace
- [ ] Check Cloud Run logs for proxy debug messages
- [ ] Verify query generation shows correct number of URLs
- [ ] Check that reference data has valid `newsroom_url` values
- [ ] Confirm Bright Data zone is active and has credits
- [ ] Test proxy directly with curl (see below)

---

### Testing Bright Data Proxy Directly

Test the proxy connection without the pipeline:

```bash
# Test with a simple Google search
curl -x "http://user:pass@brd.superproxy.io:33335" \
  "https://www.google.com/search?q=test&brd_json=1"
```

Expected response: JSON with search results

If this fails, the issue is with:
- Bright Data credentials
- Bright Data zone configuration
- Network connectivity to Bright Data

---

### Getting Help

If issues persist:

1. Check Cloud Run logs:
   ```bash
   gcloud run services logs tail press-release-collector --region=us-central1 --limit=100
   ```

2. View specific log entries:
   ```bash
   gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=press-release-collector" --limit=50 --format=json
   ```

3. Check Bright Data dashboard:
   - Verify zone status
   - Check usage and credits
   - Review request logs

4. Test locally:
   ```bash
   python main_cli.py --start-date 2026-02-10 --end-date 2026-02-11 --skip-scraping
   ```

---

## Prevention

To avoid these issues in the future:

1. **Always use `echo -n`** when creating secrets (avoids trailing newlines)
2. **Run `verify_proxy.py`** before deploying
3. **Check logs** after each deployment for proxy debug messages
4. **Keep reference data clean** - validate newsroom URLs regularly

---

## Recent Fixes

**2026-02-12:**
- Added proxy URL validation and debugging
- Added query validation to skip empty URLs
- Created `verify_proxy.py` diagnostic tool
- Updated Secret Manager with properly formatted proxy URL
- Added detailed debug logging to SERP collection
