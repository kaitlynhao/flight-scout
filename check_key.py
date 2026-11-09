"""Checks that your SerpApi key works. Uses SerpApi's free Account API, so it costs 0 searches."""
import os

import requests
from dotenv import load_dotenv

load_dotenv()  # reads SERPAPI_KEY from the hidden .env file
key = os.getenv("SERPAPI_KEY", "").strip()

if not key:
    print("No key found. Open the .env file and paste your key after SERPAPI_KEY=")
    raise SystemExit(1)

resp = requests.get("https://serpapi.com/account.json", params={"api_key": key}, timeout=30)
if resp.status_code != 200:
    print(f"SerpApi rejected the key (status {resp.status_code}). Double-check you copied it fully.")
    raise SystemExit(1)

info = resp.json()
print("Key works!")
print(f"Plan: {info.get('plan_name')}")
print(f"Searches per month: {info.get('searches_per_month')}")
print(f"Searches left this month: {info.get('plan_searches_left')}")
