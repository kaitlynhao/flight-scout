"""Test: a real round trip, JFK <-> LAX, Dec 10-17 (uses 7 searches)."""
import flights
from scoring import DEFAULT_PREFS
from scout import SHOW_TOP_WEB, describe_card, describe_returns, estimate_searches, find_trips, pick_top
from usage import get_usage

trip = {"origin": "JFK", "destination": "LAX", "depart_date": "2026-12-10", "return_date": "2026-12-17"}
prefs = {**DEFAULT_PREFS, "max_price": 450, "target_price": 300}

before = get_usage()
print(f"Estimated cost: {estimate_searches(trip)} searches. SerpApi says used so far: {before['used']}\n")
result = find_trips(trip, prefs)
n_rt = sum(1 for t in result["trips"] if t["ticket"] == "round_trip")
n_sep = len(result["trips"]) - n_rt
print(f"{len(result['ow_out'])} one-way flights out, {len(result['ow_back'])} back.")
print(f"Round-trip prices checked for: " + ", ".join(
    f"{t[0][0][:6]} {t[-1][-5:]}" for t in result["rt_checked"]))
print(f"{len(result['trips'])} trips scored ({n_rt} round-trip tickets, {n_sep} as 2 separate tickets), "
      f"grouped into {len(result['cards'])} cards.\n")

top = pick_top(result, SHOW_TOP_WEB)
print(f"TOP {len(top)}:")
for c in top:
    print("\n".join(describe_card(c)))

print("\nMore returns for the #1 card's outbound flight:")
print("\n".join(describe_returns(result, top[0]["pairs"][0]["out"])))

after = get_usage()
print(f"\nSearches this run: {flights.searches_this_run}. SerpApi's counter: {after['used']} / {after['plan_total']} (it runs behind)")
