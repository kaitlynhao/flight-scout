"""Tests flexible dates with a FAKE search. Costs 0 searches."""
import scout
from scoring import DEFAULT_PREFS


def f(airline, day, time, price, token=None):
    return {"airlines": [airline], "stops": 0, "layovers": [], "depart": f"{day} {time}",
            "arrive": f"{day} 23:59", "price": price, "duration_min": 330,
            "departure_token": token, "flight_numbers": [f"{airline[:2]}{day[-2:]}{time}"]}


# Dec 9 is $60 cheaper to leave; return prices same every day
OUT = {"2026-12-09": 130, "2026-12-10": 190, "2026-12-11": 185}
calls = []
def fake_search(trip, origin, destination, day, return_date=None, token=None):
    calls.append((origin, day, return_date, token))
    if token:
        return [f("Delta", trip["return_date"], "12:00", OUT[trip["depart_date"]] + 200)]
    if return_date:
        return [f("Delta", day, "10:00", OUT[day] + 200, token=f"tok{day}")]
    if origin == "JFK":
        return [f("Delta", day, "10:00", OUT[day])]
    return [f("Delta", day, "12:00", 200)]

scout._search = fake_search
scout.check_budget = lambda needed, limit=900: None
trip = {"origin": "JFK", "destination": "LAX", "depart_date": "2026-12-10", "return_date": "2026-12-17",
        "depart_flex": 1, "return_flex": 1}
prefs = {**DEFAULT_PREFS, "max_price": 500, "target_price": 300}

print("Estimated searches:", scout.estimate_searches(trip))
r = scout.find_trips(trip, prefs)
print("Searches made:", len(calls))
assert len(calls) <= scout.estimate_searches(trip) == 11  # estimate is a maximum
print("Round-trip prices checked for:", r["rt_trip"]["depart_date"], "to", r["rt_trip"]["return_date"])
for c in scout.pick_top(r, 4):
    print("\n".join(scout.describe_card(c)))
    print("     ", " | ".join(x for x in c["reasons"] if "day" in x) or "(your exact dates)")
best = r["cards"][0]
assert best["pairs"][0]["out"]["depart"].startswith("2026-12-09"), "Dec 9 is $60 cheaper -> should win despite -2"
exact = [c for c in r["cards"] if c["pairs"][0]["out"]["depart"].startswith("2026-12-10")
         and c["pairs"][0]["returns"][0]["depart"].startswith("2026-12-17")]
assert exact and all("day" not in " ".join(c["reasons"]) for c in exact)
print("\nFlexible-date checks passed.")
