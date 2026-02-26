"""
Test Bright Data SERP API Connection
=====================================

This script tests both methods of connecting to Bright Data:
1. Using the Bright Data SDK (recommended)
2. Using raw requests with proxy (fallback)
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

proxy_url = os.getenv('BRIGHT_DATA_PROXY_URL')

print("=" * 70)
print("BRIGHT DATA CONNECTION TEST")
print("=" * 70)
print()

# Mask password for logging
def mask_password(url):
    if '@' in url:
        creds, rest = url.split('@', 1)
        if ':' in creds:
            protocol_user, password = creds.rsplit(':', 1)
            return f"{protocol_user}:***@{rest}"
    return url

print(f"Proxy URL: {mask_password(proxy_url)}")
print()

# Test 1: Raw requests with proxy
print("Test 1: Raw HTTP Request with Proxy")
print("-" * 70)
try:
    test_url = "https://www.google.com/search?q=test&brd_json=1"
    print(f"Request URL: {test_url}")
    print(f"Using proxy: {mask_password(proxy_url)}")
    print()

    response = requests.get(
        test_url,
        proxies={
            'http': proxy_url,
            'https': proxy_url
        },
        timeout=15
    )

    print(f"✅ Status Code: {response.status_code}")
    print(f"✅ Response Length: {len(response.text)} bytes")
    print(f"✅ Content-Type: {response.headers.get('content-type')}")

    # Check if it's JSON
    if 'json' in response.headers.get('content-type', ''):
        print(f"✅ Response is JSON (SERP API working)")
        import json
        data = json.loads(response.text)
        print(f"   Keys: {list(data.keys())}")
    else:
        print(f"⚠️  Response is HTML (not JSON - may not be SERP zone)")
        print(f"   First 200 chars: {response.text[:200]}")

    print()
    print("✅ Test 1 PASSED: Raw proxy requests work")

except Exception as e:
    print(f"❌ Test 1 FAILED: {type(e).__name__}: {str(e)}")
    print()

print()
print()

# Test 2: Bright Data SDK (if available)
print("Test 2: Bright Data SDK")
print("-" * 70)
try:
    from brightdata.client import BrightDataClient

    # Extract credentials from proxy URL
    # Format: http://username:password@host:port
    if '@' in proxy_url:
        creds_part = proxy_url.split('//')[1].split('@')[0]
        username, password = creds_part.split(':', 1)

        print(f"Initializing BrightDataClient...")
        client = BrightDataClient(
            auth_token=password,  # Using password as auth token
            serp_zone="corporate_newsroom_collection"  # Zone name from proxy URL
        )

        print(f"Testing search with SDK...")
        result = client.search(
            query="test",
            search_engine="google",
            response_format="json",
            parse=True
        )

        print(f"✅ SDK search successful")
        print(f"   Result type: {type(result)}")
        if isinstance(result, dict):
            print(f"   Keys: {list(result.keys())}")

        print()
        print("✅ Test 2 PASSED: Bright Data SDK works")

except ImportError:
    print("⚠️  Bright Data SDK not installed or wrong version")
    print("   pip install brightdata-sdk")
except Exception as e:
    print(f"❌ Test 2 FAILED: {type(e).__name__}: {str(e)}")

print()
print("=" * 70)
print("TEST COMPLETE")
print("=" * 70)
print()
print("Recommendations:")
print("- If Test 1 passed: Your zone works with raw proxy requests")
print("- If Test 2 passed: Use the Bright Data SDK for better reliability")
print("- If both failed: Check your Bright Data account and zone configuration")
