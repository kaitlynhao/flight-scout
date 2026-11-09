"""Test: one real one-way search (costs 1 SerpApi search)."""
from flights import search_flights


def fmt_minutes(m):
    return f"{m // 60}h {m % 60:02d}m" if m else "?"


flights, url = search_flights("JFK", "LAX", "2026-12-10")
print(f"Searches used: 1   |   Flights found: {len(flights)}\n")
for f in flights[:5]:
    stops = "nonstop" if f["stops"] == 0 else f"{f['stops']} stop(s) via " + ", ".join(l["airport"] for l in f["layovers"])
    print(f"${f['price']}  {fmt_minutes(f['duration_min'])}  {stops}  {', '.join(f['airlines'])}")
    print(f"    departs {f['depart']}  ->  arrives {f['arrive']}")
print(f"\nSee it on Google Flights: {url}")
