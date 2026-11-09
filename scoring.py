"""The "you'd like this" score.

STEP 1 - LIMITS (dealbreakers, with leniency you choose):
    max price, max travel time, max stops, departure time windows.
    Inside a limit       -> fine.
    Within your leniency -> allowed, but loses some points.
    Beyond your leniency -> the trip is HIDDEN (it breaks a rule you set).

STEP 2 - SCORE (for trips that pass the limits):
    Start at 100 and lose points for price, travel time and stops.
    YOUR priorities (1-5) decide how many points each category can take away.

Your preferences (a dictionary):
    max_price           most you'd pay for the whole trip (USD)
    price_leeway        how many $ over max_price you'd still consider
    target_price        "great deal" price -> urgent alert at or below this
    max_hours           longest travel time per direction (hours)
    time_leeway_min     how many minutes over max_hours you'd still consider
    max_stops           0 = nonstop only, 1 = up to 1 stop, None = any number of stops
    outbound_window     ("08:00", "20:00") acceptable departure times going out, or None = any time
    return_window       same, for the flight home, or None
    window_leeway_min   minutes of wiggle room outside each window
    price_importance    1-5 how much price matters
    time_importance     1-5 how much travel time matters
    stops_importance    1-5 how much avoiding stops matters
    allow_separate      True = also consider 2 separate one-way tickets
    great_score         urgent alert when a trip scores at or above this
"""

WINDOW_LEEWAY_PENALTY = 5   # points lost for departing within the window leniency
LONG_LAYOVER_MIN = 4 * 60   # layovers this long (minutes) count as "long"
TIGHT_LAYOVER_MIN = 50      # layovers shorter than this are "risky connections"

DEFAULT_PREFS = {
    "max_price": 300, "price_leeway": 0, "target_price": 180,
    "max_hours": 7, "time_leeway_min": 0, "max_stops": None,
    "outbound_window": None, "return_window": None, "window_leeway_min": 30,
    "price_importance": 3, "time_importance": 3, "stops_importance": 3,
    "allow_separate": True, "great_score": 90,
}

PRIORITY_PRESETS = {  # "What matters most?" -> price / time / stops importance
    "Balanced": (3, 3, 3),
    "Cheapest": (5, 2, 2),
    "Fastest": (2, 5, 2),
    "Fewest stops": (2, 2, 5),
}


def _hm(minutes):
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _clock(mins):
    """Minutes since midnight -> '7:40 AM'."""
    h, m = divmod(mins % (24 * 60), 60)
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _minutes_outside(t, start, end):
    """How many minutes departure time t is outside the window (0 = inside).
    Handles windows that cross midnight, like 21:00-02:00."""
    day = 24 * 60
    inside = start <= t <= end if start <= end else (t >= start or t <= end)
    if inside:
        return 0
    return min((start - t) % day, (t - end) % day)


# ---------- STEP 1: limits. Each returns ("ok" | "lenient" | "out", reason) ----------

def _check_price(price, prefs):
    mx, lee = prefs["max_price"], prefs.get("price_leeway", 0)
    if price is None:
        return "out", f"✗ no price listed"
    if price <= mx:
        return "ok", None
    if price <= mx + lee:
        return "lenient", f"${price} is ${price - mx} over your ${mx} max (within your ${lee} leniency)"
    return "out", f"✗ ${price} is over your ${mx} max + ${lee} leniency"


def _check_time(label, minutes, prefs):
    limit, lee = prefs["max_hours"] * 60, prefs.get("time_leeway_min", 0)
    if minutes is None or minutes <= limit:
        return "ok", None
    if minutes <= limit + lee:
        return "lenient", f"{label}{_hm(minutes)} is {minutes - limit} min over your {prefs['max_hours']}h max (within your {lee}-min leniency)"
    return "out", f"✗ {label}{_hm(minutes)} is over your {prefs['max_hours']}h max + {lee}-min leniency"


def _check_stops(label, flight, prefs):
    mx = prefs.get("max_stops")
    if mx is None or flight["stops"] <= mx:
        return "ok", None
    allowed = "nonstop only" if mx == 0 else f"up to {mx} stop"
    return "out", f"✗ {label}{flight['stops']} stop(s), you chose {allowed}"


def _check_window(label, flight, window, leeway):
    if not window or not flight.get("depart"):
        return "ok", None
    start, end = _to_minutes(window[0]), _to_minutes(window[1])
    t = _to_minutes(flight["depart"].split(" ")[1])  # "2026-12-10 21:35" -> 21:35
    span = f"{_clock(start)}-{_clock(end)}"
    off_by = _minutes_outside(t, start, end)
    if off_by == 0:
        return "ok", f"{label}departs {_clock(t)}, inside your {span} window"
    if off_by <= leeway:
        return "lenient", f"{label}departs {_clock(t)}, {off_by} min outside your {span} window (within your {leeway}-min leniency)"
    return "out", f"✗ {label}departs {_clock(t)}, {off_by} min outside your {span} window + {leeway}-min leniency"


# ---------- STEP 2: score. Each category gives a "badness" 0 (perfect) .. 1 (worst) ----------

def _price_badness(price, prefs):
    target, mx = prefs["target_price"], prefs["max_price"]
    if price <= target:
        return 0.0, f"${price} is at or below your ${target} target"
    if price <= mx:
        return 0.6 * (price - target) / max(mx - target, 1), \
            f"${price} is under your ${mx} max but above your ${target} target"
    # Within price leniency: 0.6 at max, up to 0.9 at the edge of your leniency
    lee = max(prefs.get("price_leeway", 0), 1)
    return 0.6 + 0.3 * min((price - mx) / lee, 1), None  # reason already given by the limit check


def _time_badness(label, minutes, prefs):
    limit = prefs["max_hours"] * 60
    if minutes is None:
        return 0.0, None
    if minutes <= limit:
        return 0.0, f"{label}{_hm(minutes)} is within your {prefs['max_hours']}h limit"
    lee = max(prefs.get("time_leeway_min", 0), 1)
    return 0.5 * min((minutes - limit) / lee, 1), None  # reason already given by the limit check


def _stops_badness(label, flight):
    stops = flight["stops"]
    if stops == 0:
        return 0.0, [f"{label}nonstop"]
    bad = 0.4 if stops == 1 else 0.8
    notes = [f"{label}{stops} stop(s)"]
    for lay in flight["layovers"]:
        mins, where = lay.get("minutes") or 0, lay.get("airport")
        if lay.get("overnight"):
            bad += 0.3
            notes.append(f"{label}overnight layover in {where}")
        elif mins >= LONG_LAYOVER_MIN:
            bad += 0.15
            notes.append(f"{label}long layover in {where} ({_hm(mins)})")
        elif 0 < mins < TIGHT_LAYOVER_MIN:
            bad += 0.1
            notes.append(f"{label}tight {mins}-min connection in {where}")
    return min(bad, 1.0), notes


def category_points(prefs):
    """How many points each category can take away, based on your priorities.
    The three always add up to 100. Example: all set to 3 -> 33 / 33 / 33."""
    w = {k: max(1, int(prefs[f"{k}_importance"])) for k in ("price", "time", "stops")}
    total = sum(w.values())
    return {k: 100 * v / total for k, v in w.items()}


def score_trip(legs, total_price, prefs):
    """Score a one-way flight or a full round trip.

    legs: list of flights (1 for one-way; [outbound, return] for round trip)
    total_price: the price for the whole trip
    Returns (score, reasons). score is None when the trip breaks one of your limits
    beyond your leniency (it gets hidden); the reasons then say which limit (lines start with ✗).
    """
    p = {**DEFAULT_PREFS, **prefs}
    labels = [""] if len(legs) == 1 else ["Departure: ", "Return: "]
    windows = [p.get("outbound_window"), p.get("return_window")]

    # STEP 1: limits
    checks = [_check_price(total_price, p)]
    for label, flight, window in zip(labels, legs, windows):
        checks.append(_check_time(label, flight["duration_min"], p))
        checks.append(_check_stops(label, flight, p))
        checks.append(_check_window(label, flight, window, p["window_leeway_min"]))
    broken = [why for status, why in checks if status == "out"]
    if broken:
        return None, broken

    # STEP 2: score
    pts = category_points(p)
    reasons = []

    def explain(off, text):
        if text:
            reasons.append(f"-{off}: {text}" if off else f"+ {text}")

    price_status, price_why = checks[0]
    bad, text = _price_badness(total_price, p)
    price_off = round(bad * pts["price"])
    explain(price_off, text or price_why)

    time_off = stops_off = window_off = 0
    for i, (label, flight) in enumerate(zip(labels, legs)):
        time_status, time_why = checks[1 + i * 3]
        bad, text = _time_badness(label, flight["duration_min"], p)
        off = round(bad * pts["time"] / len(legs))
        time_off += off
        explain(off, text or time_why)

        bad, notes = _stops_badness(label, flight)
        off = round(bad * pts["stops"] / len(legs))
        stops_off += off
        explain(off, ", ".join(notes))

        win_status, win_why = checks[3 + i * 3]
        off = WINDOW_LEEWAY_PENALTY if win_status == "lenient" else 0
        window_off += off
        explain(off, win_why)

    score = 100 - price_off - time_off - stops_off - window_off
    return max(0, min(100, score)), reasons


def score_flight(flight, prefs):
    """Shortcut for a one-way flight (or an outbound flight before we know the return)."""
    return score_trip([flight], flight["price"], prefs)
