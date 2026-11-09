"""Tests limits (with leniency) and scoring on made-up flights. Costs 0 searches."""
from scoring import DEFAULT_PREFS, PRIORITY_PRESETS, score_flight, score_trip


def fake(price, hours, stops=0, depart="12:00"):
    return {"price": price, "duration_min": int(hours * 60), "stops": stops,
            "layovers": [{"airport": "DEN", "minutes": 90, "overnight": False}] * stops,
            "depart": f"2026-12-10 {depart}"}


def show(name, flight, prefs):
    s, why = score_flight(flight, prefs)
    print(f"{name:42} {'HIDDEN' if s is None else f'{s}/100'}")
    for r in why:
        print(f"      {r}")
    return s


base = {**DEFAULT_PREFS, "max_price": 300, "price_leeway": 50, "target_price": 180,
        "max_hours": 7, "time_leeway_min": 30, "max_stops": 1,
        "outbound_window": ("08:00", "20:00"), "window_leeway_min": 30}

print("=== PRICE: max $300, leniency $50 ===")
a = show("$250 (inside)", fake(250, 6), base)
b = show("$320 (within leniency)", fake(320, 6), base)
c = show("$360 (beyond leniency)", fake(360, 6), base)
assert a > b and c is None

print("\n=== TRAVEL TIME: max 7h, leniency 30 min ===")
a = show("6h 30m (inside)", fake(200, 6.5), base)
b = show("7h 20m (within leniency)", fake(200, 7 + 20 / 60), base)
c = show("8h (beyond leniency)", fake(200, 8), base)
assert a > b and c is None

print("\n=== STOPS ===")
for label, mx in [("Nonstop only", 0), ("Up to 1 stop", 1), ("Any number", None)]:
    p = {**base, "max_stops": mx}
    res = [score_flight(fake(200, 6, stops=n), p)[0] for n in (0, 1, 2)]
    print(f"{label:14} nonstop={res[0]}  1 stop={res[1]}  2 stops={res[2]}")
assert score_flight(fake(200, 6, 1), {**base, "max_stops": 0})[0] is None
assert score_flight(fake(200, 6, 2), {**base, "max_stops": 1})[0] is None
assert score_flight(fake(200, 6, 2), {**base, "max_stops": None})[0] is not None

print("\n=== DEPARTURE WINDOW 8 AM-8 PM, leniency 30 min ===")
a = show("12:00 PM (inside)", fake(200, 6, depart="12:00"), base)
b = show("7:40 AM (within leniency)", fake(200, 6, depart="07:40"), base)
c = show("6:00 AM (beyond leniency)", fake(200, 6, depart="06:00"), base)
assert a - b == 5 and c is None

print("\n=== ZERO LENIENCY: $301 vs a $300 max ===")
assert score_flight(fake(301, 6), {**base, "price_leeway": 0})[0] is None
print("$301 hidden ✓")

print("\n=== ROUND TRIP: return breaks the time limit -> whole trip hidden ===")
s, why = score_trip([fake(0, 6), fake(0, 9)], 280, base)
print(s, why)
assert s is None

print("\n=== PRIORITY PRESETS: cheap 1-stop vs mid-price nonstop ===")
for name, (pi, ti, si) in PRIORITY_PRESETS.items():
    p = {**base, "price_importance": pi, "time_importance": ti, "stops_importance": si}
    cheap = score_flight(fake(150, 6.75, stops=1), p)[0]
    mid = score_flight(fake(240, 6), p)[0]
    print(f"{name:13} cheap 1-stop={cheap}  mid-price nonstop={mid}")
p = {**base, **dict(zip(("price_importance", "time_importance", "stops_importance"), PRIORITY_PRESETS["Cheapest"]))}
assert score_flight(fake(150, 6.75, stops=1), p)[0] > score_flight(fake(240, 6), p)[0]
print("\nAll scoring checks passed.")
