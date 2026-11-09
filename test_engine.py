"""Tests the full engine with a FAKE search (made-up flights). Costs 0 searches."""
import scout
from scoring import DEFAULT_PREFS


def f(airline, time, price, stops=0, minutes=330, token=None, day="2026-12-10"):
    return {"airlines": [airline], "stops": stops, "layovers": [{"airport": "DEN", "minutes": 90}] * stops,
            "depart": f"{day} {time}", "price": price, "duration_min": minutes,
            "departure_token": token, "flight_numbers": [f"{airline[:2]}{time}"]}


def back(airline, time, price, stops=0):
    return f(airline, time, price, stops, day="2026-12-17")


# One-way prices
OW_OUT = [f("JetBlue", "20:35", 164, minutes=385), f("JetBlue", "21:35", 164, minutes=380),
          f("Delta", "07:00", 180), f("American", "09:00", 175), f("United", "13:00", 150, stops=1, minutes=420),
          f("Spirit", "05:00", 90, stops=2, minutes=600)]
OW_BACK = [back("Delta", "11:00", 199), back("JetBlue", "14:35", 199), back("American", "14:08", 210),
           back("United", "09:00", 160, stops=1)]
# Round-trip outbound list (same flights, round-trip "from" prices, with tokens)
RT_OUT = [dict(x, price=p, departure_token=tok) for x, p, tok in zip(
    OW_OUT, [363, 363, 392, 387, 380, 250], ["jb2035", "jb2135", "dl", "aa", "ua", "nk"])]
RT_RETURNS = {
    "jb2135": [back("JetBlue", "14:35", 340), back("JetBlue", "16:00", 363)],
    "jb2035": [back("JetBlue", "14:35", 345)],
    "dl": [back("Delta", "11:00", 420)],      # 2 one-ways = 180 + 199 = 379 -> cheaper
    "aa": [back("American", "14:08", 385)],   # 2 one-ways = 175 + 210 = 385 -> same
    "ua": [back("United", "09:00", 310, stops=1)],
}
calls = []
def fake_search(trip, origin, destination, date, return_date=None, token=None):
    calls.append(token or ("rt-list" if return_date else f"one-way {origin}"))
    if token:
        return RT_RETURNS.get(token, [])
    if return_date:
        return RT_OUT
    return OW_BACK if origin == "LAX" else OW_OUT

scout._search = fake_search
scout.check_budget = lambda needed, limit=900: None

prefs = {**DEFAULT_PREFS, "max_price": 450, "target_price": 300}
trip = {"origin": "JFK", "destination": "LAX", "depart_date": "2026-12-10", "return_date": "2026-12-17"}
result = scout.find_trips(trip, prefs)

print("Searches:", calls, "\n")
assert len(calls) == 7
checked = [c for c in calls if c in RT_RETURNS or c == "nk"]
assert "jb2135" in checked and "jb2035" not in checked, "JetBlue: should check the shorter 9:35 flight"
assert len(set(checked)) == 4

print("TOP RESULTS (web shows 10):")
for c in scout.pick_top(result):
    print("\n".join(scout.describe_card(c)))
cards = result["cards"]

jb = next(c for c in cards if c["ticket"] == "round_trip" and c["price"] == 340)
assert "cheaper than booking these flights as 2 one-ways" in jb["note"]
dl = next(c for c in cards if c["pairs"][0]["out"]["airlines"] == ["Delta"] and c["pairs"][0]["returns"][0]["airlines"] == ["Delta"])
assert dl["ticket"] == "separate" and dl["price"] == 379 and "$41 cheaper than the round-trip" in dl["note"]
aa = next(c for c in cards if c["pairs"][0]["out"]["airlines"] == ["American"] and c["pairs"][0]["returns"][0]["airlines"] == ["American"])
assert aa["ticket"] == "round_trip" and aa["note"] is None, "same price -> keep the single ticket"

print("\nMore returns for JetBlue 9:35 PM:")
print("\n".join(scout.describe_returns(result, OW_OUT[1])))

print("\nTap JetBlue 8:35 PM (free, from one-way data):")
print("\n".join(scout.describe_returns(result, OW_OUT[0])))
print("Round-trip price checked yet?", scout.round_trip_checked(result, OW_OUT[0]))
used = scout.check_round_trip_prices(result, OW_OUT[0])
print(f"\nPressed 'Check round-trip ticket prices' -> used {used} search")
print("\n".join(scout.describe_returns(result, OW_OUT[0])))
assert used == 1 and scout.check_round_trip_prices(result, OW_OUT[0]) == 0, "second press should be free"
assert len(calls) == 8

no_sep = scout.find_trips(trip, {**prefs, "allow_separate": False})
assert all(c["ticket"] == "round_trip" for c in no_sep["cards"])
print(f"\nSeparate tickets OFF: {len(no_sep['cards'])} cards, all single round-trip tickets")
assert len(scout.pick_top(result, n=5)) == 5
print("\nAll engine checks passed.")
