"""
Quick script to dump the raw Discovery API response so we can see the actual field names.
"""
import json
import os
import sys
from dotenv import load_dotenv
load_dotenv()
import httpx

NVM_API_KEY = os.getenv("NVM_API_KEY", "")
if not NVM_API_KEY:
    print("ERROR: NVM_API_KEY missing from .env")
    sys.exit(1)

url = "https://nevermined.ai/hackathon/register/api/discover"
with httpx.Client(timeout=30.0) as client:
    resp = client.get(url, headers={"x-nvm-api-key": NVM_API_KEY})
    resp.raise_for_status()
    data = resp.json()

print("=== TOP-LEVEL TYPE ===")
print(type(data))

if isinstance(data, dict):
    print("\n=== TOP-LEVEL KEYS ===")
    for k, v in data.items():
        print(f"  {k!r}: {type(v).__name__} ", end="")
        if isinstance(v, list):
            print(f"(len={len(v)})")
        else:
            print(f"= {str(v)[:80]}")

if isinstance(data, list):
    entries = data
elif isinstance(data, dict):
    # flatten all lists
    entries = []
    for v in data.values():
        if isinstance(v, list):
            entries.extend(v)
else:
    entries = []

print(f"\n=== TOTAL ENTRIES: {len(entries)} ===")

if entries:
    print("\n=== FIRST ENTRY (full) ===")
    print(json.dumps(entries[0], indent=2))

    print("\n=== ALL KEYS ACROSS ALL ENTRIES ===")
    all_keys = set()
    for e in entries:
        if isinstance(e, dict):
            all_keys.update(e.keys())
    print(sorted(all_keys))

    print("\n=== FIRST 5 ENTRIES (compact) ===")
    for e in entries[:5]:
        print(json.dumps(e, indent=2))
        print("---")

# Save full dump
with open("discovery_raw.json", "w") as f:
    json.dump(data, f, indent=2)
print("\nFull raw response saved to: discovery_raw.json")