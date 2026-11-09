"""Search counter + budget guard.

Asks SerpApi's free Account API how many searches you've used this month (costs 0 searches),
and refuses to start a search that would go over your monthly limit.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MONTHLY_LIMIT = int(os.getenv("MONTHLY_SEARCH_LIMIT", "900"))  # stay safely under 1,000


class BudgetError(Exception):
    """Raised when a search would go over your monthly limit."""


def get_usage():
    """Returns a small dict: used, left, plan_total, renews. Costs 0 searches."""
    key = os.getenv("SERPAPI_KEY", "").strip()
    d = requests.get("https://serpapi.com/account.json", params={"api_key": key}, timeout=30).json()
    if "error" in d:
        raise RuntimeError(f"SerpApi said: {d['error']}")
    return {
        "used": d.get("this_month_usage", 0),
        "left": d.get("total_searches_left", 0),
        "plan_total": d.get("searches_per_month", 0),
        "renews": d.get("plan_renewal_date"),
    }


def check_budget(needed, monthly_limit=DEFAULT_MONTHLY_LIMIT):
    """Stop BEFORE searching if `needed` more searches would pass your limit."""
    u = get_usage()
    if u["used"] + needed > monthly_limit:
        raise BudgetError(
            f"This needs {needed} search(es), but you've used {u['used']} of your "
            f"{monthly_limit} limit this month (resets {u['renews']})."
        )
    return u
