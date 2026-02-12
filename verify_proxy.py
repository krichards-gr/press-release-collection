"""
Verify Bright Data Proxy Configuration
========================================

This script helps diagnose proxy configuration issues.
Run this locally to verify your proxy settings before deploying.
"""

import os
import sys
from dotenv import load_dotenv
from config import config

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Load environment variables
load_dotenv()

print("=" * 70)
print("BRIGHT DATA PROXY CONFIGURATION VERIFICATION")
print("=" * 70)
print()

# Check what's configured
print("1. Environment Variables:")
print(f"   BRIGHT_DATA_PROXY_URL: {os.getenv('BRIGHT_DATA_PROXY_URL', '(not set)')}")
print(f"   BRIGHT_DATA_PROXY_URL_HTTP: {os.getenv('BRIGHT_DATA_PROXY_URL_HTTP', '(not set)')}")
print(f"   BRIGHT_DATA_PROXY_URL_HTTPS: {os.getenv('BRIGHT_DATA_PROXY_URL_HTTPS', '(not set)')}")
print()

print("2. Config Values (after processing):")

def mask_password(url):
    """Mask password in proxy URL for safe logging"""
    if not url:
        return "(empty)"
    if '@' in url:
        creds, rest = url.split('@', 1)
        if ':' in creds:
            protocol_user, password = creds.rsplit(':', 1)
            return f"{protocol_user}:***@{rest}"
    return url

print(f"   HTTP:  {mask_password(config.BRIGHT_DATA_PROXY_URL_HTTP)}")
print(f"   HTTPS: {mask_password(config.BRIGHT_DATA_PROXY_URL_HTTPS)}")
print()

# Validation checks
print("3. Validation Checks:")

issues = []

# Check if URLs are set
if not config.BRIGHT_DATA_PROXY_URL_HTTP:
    issues.append("❌ HTTP proxy URL is empty")
else:
    print(f"   ✅ HTTP proxy URL is set")

if not config.BRIGHT_DATA_PROXY_URL_HTTPS:
    issues.append("❌ HTTPS proxy URL is empty")
else:
    print(f"   ✅ HTTPS proxy URL is set")

# Check URL format
if config.BRIGHT_DATA_PROXY_URL_HTTP:
    if not config.BRIGHT_DATA_PROXY_URL_HTTP.startswith('http://'):
        issues.append(f"❌ HTTP proxy URL doesn't start with 'http://' (starts with: '{config.BRIGHT_DATA_PROXY_URL_HTTP[:20]}...')")
    else:
        print(f"   ✅ HTTP proxy URL has correct protocol")

if config.BRIGHT_DATA_PROXY_URL_HTTPS:
    if not config.BRIGHT_DATA_PROXY_URL_HTTPS.startswith('http://'):
        issues.append(f"❌ HTTPS proxy URL doesn't start with 'http://' (starts with: '{config.BRIGHT_DATA_PROXY_URL_HTTPS[:20]}...')")
    else:
        print(f"   ✅ HTTPS proxy URL has correct protocol")

# Check for credentials
if config.BRIGHT_DATA_PROXY_URL_HTTP and '@' not in config.BRIGHT_DATA_PROXY_URL_HTTP:
    issues.append("❌ HTTP proxy URL missing credentials (no '@' found)")
else:
    if config.BRIGHT_DATA_PROXY_URL_HTTP:
        print(f"   ✅ HTTP proxy URL contains credentials")

# Check for whitespace
if config.BRIGHT_DATA_PROXY_URL_HTTP and config.BRIGHT_DATA_PROXY_URL_HTTP != config.BRIGHT_DATA_PROXY_URL_HTTP.strip():
    issues.append("❌ HTTP proxy URL has leading/trailing whitespace")

if config.BRIGHT_DATA_PROXY_URL_HTTPS and config.BRIGHT_DATA_PROXY_URL_HTTPS != config.BRIGHT_DATA_PROXY_URL_HTTPS.strip():
    issues.append("❌ HTTPS proxy URL has leading/trailing whitespace")

print()

# Summary
if issues:
    print("4. Issues Found:")
    for issue in issues:
        print(f"   {issue}")
    print()
    print("=" * 70)
    print("❌ PROXY CONFIGURATION HAS ISSUES")
    print("=" * 70)
    print()
    print("To fix:")
    print("1. Update your .env file with the correct proxy URL:")
    print("   BRIGHT_DATA_PROXY_URL=http://brd-customer-xxx-zone-xxx:password@brd.superproxy.io:33335")
    print()
    print("2. For Cloud Run, update the secret:")
    print("   echo -n 'http://brd-customer-xxx-zone-xxx:password@brd.superproxy.io:33335' | \\")
    print("   gcloud secrets versions add bright-data-proxy-url --data-file=-")
    print()
    print("   (Make sure to use 'echo -n' to avoid adding a newline)")
    exit(1)
else:
    print("4. Summary:")
    print("   ✅ All checks passed!")
    print()
    print("=" * 70)
    print("✅ PROXY CONFIGURATION LOOKS GOOD")
    print("=" * 70)
    print()
    print("If you're still having issues in Cloud Run, check:")
    print("1. That the secret is properly mounted:")
    print("   gcloud run services describe press-release-collector --region=us-central1")
    print()
    print("2. View Cloud Run logs for proxy debug messages:")
    print("   gcloud run services logs tail press-release-collector --region=us-central1")
    exit(0)
