"""Flight Scout's brain: search a trip, score everything, rank it, group look-alikes.

A trip is a dictionary like:
    {"origin": "JFK", "destination": "LAX", "depart_date": "2026-12-10",
     "return_date": "2026-12-17",   # leave out (or None) for one-way
     "adults": 1, "cabin": "economy",
     "depart_flex": 1, "return_flex": 1}   # optional: also try dates up to N days earlier/later

FLEXIBLE DATES (±N days): one-way searches for every nearby date (cheap: 1 per date), every
date combination scored as 2 separate tickets; real round-trip prices checked only for the
best date pair. Each day away from your chosen date costs the trip 2 points.

SEARCH COST
  One-way trip:  1 search.
  Round trip:    7 searches =
      1  one-way flights going out
      1  one-way flights coming back      -> every out+back pair scored as "2 separate tickets"
      1  round-trip outbound list         -> gives a "token" for each outbound flight
      4  round-trip returns for 4 outbound flights (1 each) -> real "1 round-trip ticket" prices
  The 4 outbound flights checked are the ones behind the BEST full trips from the one-way
  pairs, preferring a different airline each time.

RESULTS
  find_trips() returns a "result" dictionary. Its "cards" are ranked option cards:
  look-alike trips (same ticket type, score, price, airlines, stops; only times differ)
  are grouped into one card with several time choices.
"""
from datetime import date as _date, timedelta

import flights
from scoring import score_flight, score_trip
from usage import DEFAULT_MONTHLY_LIMIT, check_budget

TOP_OUTBOUND = 4       # round trips: how many outbound flights get real round-trip prices checked
SHOW_TOP_WEB = 10      # cards shown in the web app (more behind "show more")
SHOW_TOP_REPORT = 5    # cards shown per trip in the phone report
MAX_PER_OUTBOUND = 2   # variety: at most this many shown cards may start with the same outbound flight
MORE_RETURNS = 3       # "More returns for this flight": how many extra options to list
DATE_SHIFT_PENALTY = 2 # points lost per day away from the date you picked (flexible dates)

TICKET_LABEL = {
    "one_way": "one-way",
    "round_trip": "1 round-trip ticket",
    "separate": "🎟️ 2 separate tickets",
}


def _dates(base, flex):
    """All dates within ±flex days of base (skipping dates in the past), base date first."""
    d0 = _date.fromisoformat(base)
    out = [d0] + [d0 + timedelta(days=k) for i in range(1, int(flex or 0) + 1) for k in (-i, i)]
    return [d.isoformat() for d in out if d >= _date.today()]


def estimate_searches(trip, top_n=TOP_OUTBOUND):
    n_out = len(_dates(trip["depart_date"], trip.get("depart_flex")))
    if not trip.get("return_date"):
        return n_out
    return n_out + len(_dates(trip["return_date"], trip.get("return_flex"))) + 1 + top_n


# ---------- small helpers ----------

def _search(trip, origin, destination, date, return_date=None, token=None):
    found, _ = flights.search_flights(
        origin, destination, date, return_date,
        adults=trip.get("adults", 1), cabin=trip.get("cabin", "economy"), departure_token=token,
    )
    return found


def _id(f):
    """A flight's identity across different searches: flight numbers + departure time."""
    return tuple(f.get("flight_numbers") or ()) + (f.get("depart"),)


def _looks(f):
    """What a flight 'looks like' apart from its times: airlines + number of stops."""
    return (tuple(f["airlines"]), f["stops"])


def _s(score):
    """Hidden trips have score None; sort them below everything else."""
    return score if score is not None else -1


def _rank(item):
    return (-_s(item["score"]), item["price"] if item["price"] is not None else 99999)


def _to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _representative_rank(f, prefs):
    """Which flight in a look-alike group gets its round-trip price checked (lowest wins):
    1) shortest travel time, 2) departure closest to the middle of your window,
    3) Google's own order (Python's sort keeps the original order on ties)."""
    duration = f["duration_min"] if f["duration_min"] is not None else 99999
    window = prefs.get("outbound_window")
    if not window or not f.get("depart"):
        return (duration, 0)
    start, end = _to_min(window[0]), _to_min(window[1])
    if end < start:            # window crosses midnight, e.g. 21:00-02:00
        end += 24 * 60
    middle = ((start + end) / 2) % (24 * 60)
    gap = abs(_to_min(f["depart"].split(" ")[1]) - middle)
    return (duration, min(gap, 24 * 60 - gap))


def _day(f):
    return f["depart"][:10]


def _make(legs, price, ticket, prefs, trip):
    score, reasons = score_trip(legs, price, prefs)
    if score is not None:  # flexible dates: small nudge toward the dates you picked
        wanted = [trip["depart_date"], trip.get("return_date")]
        for label, f, want in zip(["Departure", "Return"], legs, wanted):
            shift = abs((_date.fromisoformat(_day(f)) - _date.fromisoformat(want)).days) if want else 0
            if shift:
                off = DATE_SHIFT_PENALTY * shift
                score = max(0, score - off)
                when = "earlier" if _day(f) < want else "later"
                reasons.append(f"-{off}: {label.lower()} is {shift} day{'s' if shift > 1 else ''} {when} than your date")
    return {"score": score, "reasons": reasons, "price": price, "legs": legs, "ticket": ticket, "note": None}


# ---------- the main search ----------

def find_trips(trip, prefs, top_n=TOP_OUTBOUND, monthly_limit=DEFAULT_MONTHLY_LIMIT):
    """Search + score a trip. Checks your monthly budget BEFORE spending any searches.
    Returns a result dict; result["cards"] are the ranked option cards (use pick_top to show some)."""
    check_budget(estimate_searches(trip, top_n), monthly_limit)
    o, d = trip["origin"], trip["destination"]
    result = {"trip": trip, "prefs": prefs, "round_trip": bool(trip.get("return_date")), "rt_checked": {}}

    out_dates = _dates(trip["depart_date"], trip.get("depart_flex"))
    if not result["round_trip"]:
        result["outs_raw"] = [f for day in out_dates for f in _search(trip, o, d, day)]
        return rescore(result, prefs)

    # One-way searches for each date, each direction. Pair every outbound with every return.
    result["ow_out"] = [f for day in out_dates for f in _search(trip, o, d, day)]
    result["ow_back"] = [f for day in _dates(trip["return_date"], trip.get("return_flex"))
                         for f in _search(trip, d, o, day)]
    _score_separate(result)

    # Round-trip prices: only for the best date pair (or your exact dates if nothing fits)
    best = next((t for t in result["separate"] if t["score"] is not None), None)
    rt_dates = (_day(best["legs"][0]), _day(best["legs"][1])) if best else (trip["depart_date"], trip["return_date"])
    result["rt_trip"] = {**trip, "depart_date": rt_dates[0], "return_date": rt_dates[1]}
    rt = result["rt_trip"]
    result["rt_out"] = [f for f in _search(trip, o, d, rt["depart_date"], rt["return_date"])
                        if f["departure_token"]]

    # Searches 4-7: real round-trip prices for the 4 most promising outbound flights
    for f in _choose_checks(result, top_n):
        _check_rt(result, f)
    return _finish(result)


def _choose_checks(result, top_n):
    """Pick which outbound flights get real round-trip prices checked.

    Walk down the best full trips (from the one-way pairs). For each, take its outbound
    flight's look-alike group and check that group's representative (rule: shortest travel
    time, then closest to the middle of your window). First pass: only airlines we haven't
    checked yet. Second pass: fill any leftover checks with the next best, any airline.
    """
    prefs = result["prefs"]
    rt_by_id = {_id(f): f for f in result["rt_out"]}
    group_of = lambda f: (_looks(f), f["price"], score_flight(f, prefs)[0])
    groups = {}
    for f in result["ow_out"]:
        groups.setdefault(group_of(f), []).append(f)

    ordered, seen = [], set()
    rt = result["rt_trip"]
    for t in result["separate"]:
        if t["score"] is None:      # breaks your limits -> not worth a round-trip check
            continue
        if (_day(t["legs"][0]), _day(t["legs"][1])) != (rt["depart_date"], rt["return_date"]):
            continue                # round-trip prices are only checked for one date pair
        g = group_of(t["legs"][0])
        if g not in seen:
            seen.add(g)
            ordered.append(g)

    def representative(g):
        in_rt = [f for f in groups[g] if _id(f) in rt_by_id]
        if not in_rt:
            return None
        return rt_by_id[_id(min(in_rt, key=lambda f: _representative_rank(f, prefs)))]

    chosen, airlines_used = [], set()
    for first_pass in (True, False):
        for g in ordered:
            if len(chosen) == top_n:
                break
            r = representative(g)
            if r is None or any(r is c for c in chosen):
                continue
            if first_pass and tuple(r["airlines"]) in airlines_used:
                continue
            chosen.append(r)
            airlines_used.add(tuple(r["airlines"]))

    # Rare fallback: one-way flights didn't match the round-trip list well enough
    for f in sorted(result["rt_out"], key=lambda f: -_s(score_flight(f, prefs)[0])):
        if len(chosen) >= top_n:
            break
        if not any(f is c for c in chosen):
            chosen.append(f)
    return chosen


def _check_rt(result, rt_flight):
    """1 search: real round-trip ticket prices for every return paired with this outbound flight."""
    trip = result.get("rt_trip", result["trip"])
    returns = _search(trip, trip["origin"], trip["destination"], trip["depart_date"],
                      trip["return_date"], token=rt_flight["departure_token"])
    result.setdefault("rt_raw", {})[_id(rt_flight)] = (rt_flight, returns)
    _score_rt(result, _id(rt_flight))


def _score_separate(result):
    prefs = result["prefs"]
    result["separate"] = sorted(
        (_make([a, b], a["price"] + b["price"], "separate", prefs, result["trip"])
         for a in result["ow_out"] for b in result["ow_back"]
         if a["price"] is not None and b["price"] is not None
         and b["depart"] > (a.get("arrive") or a["depart"])),   # return must leave after you land
        key=_rank)


def _score_rt(result, key):
    rt_flight, returns = result["rt_raw"][key]
    # The price on each return option is the price of the WHOLE round-trip ticket
    result["rt_checked"][key] = [
        _make([rt_flight, r], r["price"], "round_trip", result["prefs"], result["trip"]) for r in returns if r["price"] is not None
    ]


def rescore(result, prefs):
    """Re-score everything with new preferences, using flights we already downloaded. Costs 0 searches."""
    result["prefs"] = prefs
    if not result["round_trip"]:
        result["one_way"] = [_make([f], f["price"], "one_way", prefs, result["trip"]) for f in result["outs_raw"] if f["price"] is not None]
    else:
        _score_separate(result)
        for key in result.get("rt_raw", {}):
            _score_rt(result, key)
    return _finish(result)


def _finish(result):
    """Combine round-trip tickets and 2-separate-ticket pairs, drop duplicates, group into cards."""
    prefs = result["prefs"]
    if not result["round_trip"]:
        trips = result["one_way"]
    else:
        merged = {}
        for checked in result["rt_checked"].values():
            for t in checked:
                merged[(_id(t["legs"][0]), _id(t["legs"][1]))] = dict(t, note=None)
        if prefs.get("allow_separate", True):
            for s in result["separate"]:
                key = (_id(s["legs"][0]), _id(s["legs"][1]))
                rt = merged.get(key)
                if rt is None:
                    merged[key] = dict(s, note=None)
                elif s["price"] < rt["price"]:  # same flights, cheaper as 2 one-ways
                    merged[key] = dict(s, note=f"${rt['price'] - s['price']} cheaper than the round-trip ticket for these exact flights")
                elif s["price"] > rt["price"]:  # same flights, cheaper as 1 round-trip ticket
                    rt["note"] = f"${s['price'] - rt['price']} cheaper than booking these flights as 2 one-ways"
        trips = list(merged.values())
    # Trips beyond your leniency are HIDDEN (kept aside so the app can offer "show them?")
    result["hidden"] = sorted((t for t in trips if t["score"] is None),
                              key=lambda t: t["price"] if t["price"] is not None else 99999)
    trips = sorted((t for t in trips if t["score"] is not None), key=_rank)
    result["trips"] = trips
    result["cards"] = _group_trips(trips, result)
    return result


def _group_trips(trips, result):
    """Turn a flat list of scored trips into option cards (look-alikes grouped together)."""
    cards = {}
    for t in trips:
        legs = t["legs"]
        key = (t["ticket"], t["score"], t["price"], t["note"], tuple(_day(f) for f in legs),
               _looks(legs[0]), _looks(legs[1]) if len(legs) > 1 else None)
        card = cards.get(key)
        if card is None:
            card = cards[key] = {
                "score": t["score"], "price": t["price"], "reasons": t["reasons"],
                "ticket": t["ticket"], "note": t["note"], "round_trip": len(legs) > 1,
                "pairs": [],              # [{"out": flight, "returns": [flights]}] - combos we actually found
                "other_departures": [],   # round-trip tickets: look-alike times NOT checked
                "trips": [],
            }
        card["trips"].append(t)
        pair = next((p for p in card["pairs"] if _id(p["out"]) == _id(legs[0])), None)
        if pair is None:
            pair = {"out": legs[0], "returns": []}
            card["pairs"].append(pair)
        if len(legs) > 1:
            pair["returns"].append(legs[1])

    # Round-trip-ticket cards: list look-alike departure times we did NOT check
    checked = set(result.get("rt_checked", {}))
    for card in cards.values():
        if card["ticket"] == "round_trip":
            out = card["pairs"][0]["out"]
            card["other_departures"] = [
                f for f in result["rt_out"]
                if _id(f) not in checked and _looks(f) == _looks(out) and f["price"] == out["price"]
                and score_flight(f, result["prefs"])[0] == score_flight(out, result["prefs"])[0]
            ]
    return sorted(cards.values(), key=_rank)


def pick_top(result, n=SHOW_TOP_WEB, max_per_outbound=MAX_PER_OUTBOUND):
    """Pick the best n cards, with at most 2 starting from the same outbound flight.
    If that leaves fewer than n, fill up with the next best anyway. Costs 0 searches."""
    cards = result["cards"]
    picked, per_out = [], {}
    for c in cards:
        k = _id(c["pairs"][0]["out"])
        if per_out.get(k, 0) < max_per_outbound:
            picked.append(c)
            per_out[k] = per_out.get(k, 0) + 1
        if len(picked) == n:
            return picked
    leftovers = [c for c in cards if not any(c is p for p in picked)]
    return sorted(picked + leftovers[: n - len(picked)], key=_rank)


# ---------- web app extras: tapping a departure time ----------

def returns_for_departure(result, out_flight):
    """Every return option we know for this outbound flight (both ticket types), best first,
    each compared with the best one. Costs 0 searches.
    Returns a list of dicts: {"trip", "is_best", "price_diff", "time_diff", "stops_diff"}."""
    options = sorted((t for t in result["trips"] if _id(t["legs"][0]) == _id(out_flight)), key=_rank)
    if not options:
        return []
    best = options[0]
    b_ret = best["legs"][1]
    rows = []
    for i, t in enumerate(options):
        r = t["legs"][1]
        rows.append({
            "trip": t, "is_best": i == 0,
            "price_diff": t["price"] - best["price"],
            "time_diff": (r["duration_min"] or 0) - (b_ret["duration_min"] or 0),
            "stops_diff": r["stops"] - b_ret["stops"],
        })
    return rows


def round_trip_checked(result, out_flight):
    """Has this outbound flight's real round-trip price already been checked?"""
    return _id(out_flight) in result["rt_checked"]


def check_round_trip_prices(result, out_flight, monthly_limit=DEFAULT_MONTHLY_LIMIT):
    """The 'Check round-trip ticket prices for this time' button. Costs 1 search
    (0 if this time was already checked). Returns the number of searches used."""
    if round_trip_checked(result, out_flight):
        return 0
    rt = next((f for f in result["rt_out"] if _id(f) == _id(out_flight)), None)
    if rt is None:
        raise ValueError("Round-trip ticket prices can only be checked for one date pair per search "
                         f"({result['rt_trip']['depart_date']} to {result['rt_trip']['return_date']}), "
                         "and Google didn't list this flight there. It can still be booked as 2 one-ways.")
    check_budget(1, monthly_limit)
    _check_rt(result, rt)
    _finish(result)
    return 1


# ---------- turning things into readable text ----------

def _clock(dt, with_day=False):
    """'2026-12-10 21:35' -> '9:35 PM' (or 'Thu Dec 10 9:35 PM' with_day)"""
    h, m = map(int, dt.split(" ")[1].split(":"))
    t = f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    return f"{_date.fromisoformat(dt[:10]).strftime('%a %b %-d')} {t}" if with_day else t


def _by_time(pairs):
    return sorted(pairs, key=lambda p: p["out"]["depart"])


def _or(times):
    return " or ".join(times)


def _leg_text(f):
    stops = "nonstop" if f["stops"] == 0 else f"{f['stops']} stop"
    return f"{', '.join(f['airlines'])} {stops}"


def describe_card(card):
    """A short, human-readable summary of an option card (list of lines)."""
    first = card["pairs"][0]["out"]
    label = TICKET_LABEL[card["ticket"]]
    if card["round_trip"]:
        back = card["pairs"][0]["returns"][0]
        title = f"{card['score']}/100  ${card['price']}  ·  {label}  ·  departure: {_leg_text(first)}  ·  return: {_leg_text(back)}"
    else:
        title = f"{card['score']}/100  ${card['price']}  ·  {label}  ·  {_leg_text(first)}"
    lines = [title]

    if not card["round_trip"]:
        lines.append(f"   Departs {_or([_clock(p['out']['depart']) for p in _by_time(card['pairs'])])}")
    else:
        rows = {}  # merge outbound times that share exactly the same return times
        for p in _by_time(card["pairs"]):
            backs = tuple(_clock(r["depart"], True) for r in sorted(p["returns"], key=lambda r: r["depart"]))
            rows.setdefault(backs, []).append(_clock(p["out"]["depart"], True))
        for backs, outs in rows.items():
            lines.append(f"   Departure {_or(outs)}  →  return {_or(list(backs))}")
        if card["other_departures"]:
            extra = ", ".join(_clock(f["depart"]) for f in sorted(card["other_departures"], key=lambda f: f["depart"]))
            lines.append(f"   Other departure times on this airline: {extra} (not checked as round trips)")
    if card["note"]:
        lines.append(f"   💡 {card['note']}")
    return lines


def _diff_text(row):
    if row["is_best"]:
        return "best return"
    parts = []
    p, t, s = row["price_diff"], row["time_diff"], row["stops_diff"]
    parts.append(f"+${p}" if p > 0 else f"${-p} cheaper" if p < 0 else "same price")
    if t:
        parts.append(f"+{t} min" if t > 0 else f"{-t} min shorter")
    if s:
        parts.append(f"+{s} stop" if s > 0 else f"{-s} fewer stop")
    if parts == ["same price"]:
        return "same price as best return"
    return " · ".join(parts) + " vs best return"


def describe_returns(result, out_flight, limit=MORE_RETURNS):
    """Readable lines for 'More returns for this flight': the best return + the next few."""
    rows = returns_for_departure(result, out_flight)
    lines = []
    for row in rows[: limit + 1]:
        t = row["trip"]
        r = t["legs"][1]
        lines.append(f"   {_clock(r['depart'])} · {_leg_text(r)} · {TICKET_LABEL[t['ticket']]} · "
                     f"{t['score']}/100 · ${t['price']} ({_diff_text(row)})")
    if len(rows) > limit + 1:
        lines.append(f"   ...{len(rows) - limit - 1} more (show all)")
    return lines
