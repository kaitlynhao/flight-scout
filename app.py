"""Flight Scout web app.  Start it with:   .venv/bin/streamlit run app.py

Search a trip right now, see every option scored for YOU (with reasons), and track trips
for the twice-daily phone reports and urgent alerts.
"""
import json
import os
import uuid
from datetime import date, time, timedelta

import pandas as pd
import streamlit as st

import flights
import scout
from scoring import DEFAULT_PREFS, PRIORITY_PRESETS, category_points
from usage import BudgetError, DEFAULT_MONTHLY_LIMIT, get_usage

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "watchlist.json")

st.set_page_config(page_title="Flight Scout", page_icon="✈️", layout="centered")

# ---------- optional password (set APP_PASSWORD in .env to turn it on) ----------
password = os.getenv("APP_PASSWORD", "").strip()
if password and not st.session_state.get("unlocked"):
    st.title("✈️ Flight Scout")
    if st.text_input("Password", type="password") == password:
        st.session_state.unlocked = True
        st.rerun()
    st.stop()

# ---------- helpers ----------

SHOW_DATES = False  # turned on for flexible-date searches, so every time also shows its day


def day(dt):
    return date.fromisoformat(dt[:10]).strftime("%a %b %-d")


def tclock(dt):
    """Time only (inside a card every time is on the same day, shown once at the top)."""
    h, m = map(int, dt.split(" ")[1].split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def clock(dt):
    h, m = map(int, dt.split(" ")[1].split(":"))
    t = f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    return f"{day(dt)}, {t}" if SHOW_DATES else t


def hm(minutes):
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes else "?"


def leg(f):
    stops = "nonstop" if f["stops"] == 0 else f"{f['stops']} stop" + (
        " via " + ", ".join(l["airport"] for l in f["layovers"]) if f["layovers"] else "")
    return f"{', '.join(f['airlines'])} · {stops} · {hm(f['duration_min'])}"


def load_watchlist():
    try:
        with open(WATCHLIST) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"trips": []}


def save_watchlist(data):
    with open(WATCHLIST, "w") as fh:
        json.dump(data, fh, indent=2)


def t2s(t):
    return t.strftime("%H:%M")


TICKET = {"one_way": "One-way", "round_trip": "1 round-trip ticket", "separate": "🎟️ 2 separate tickets"}
STOPS = {"Nonstop only": 0, "Up to 1 stop": 1, "Any number": None}

# ---------- default settings (stored in session_state so the AI can fill them in later) ----------
DEFAULTS = {
    "p_max_price": 450, "p_price_leeway": 50, "p_target_price": 350,
    "p_max_hours": 7.0, "p_time_leeway": 30, "p_stops": "Up to 1 stop",
    "p_use_windows": False, "p_out_window": (time(8), time(22)), "p_back_window": (time(8), time(22)),
    "p_window_leeway": 30, "p_preset": "Balanced",
    "p_price_imp": 3, "p_time_imp": 3, "p_stops_imp": 3,
    "p_separate": True, "p_great": 90,
    "t_type": "Round trip", "t_origin": "JFK", "t_dest": "LAX",
    "t_depart": date(2026, 12, 10), "t_return": date(2026, 12, 17), "t_adults": 1,
    "t_dflex": 0, "t_rflex": 0,
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
st.session_state.setdefault("searches_session", 0)


def apply_preset():
    preset = st.session_state.p_preset
    if preset in PRIORITY_PRESETS:
        st.session_state.p_price_imp, st.session_state.p_time_imp, st.session_state.p_stops_imp = PRIORITY_PRESETS[preset]


def sliders_changed():
    now = (st.session_state.p_price_imp, st.session_state.p_time_imp, st.session_state.p_stops_imp)
    st.session_state.p_preset = next((n for n, v in PRIORITY_PRESETS.items() if v == now), None)


def _hhmm(text):
    """'21:10' -> time(21, 0): rounded to the slider's 30-minute steps."""
    h, m = map(int, text.split(":"))
    total = round((h * 60 + m) / 30) * 30
    total = min(total, 23 * 60 + 30)
    return time(total // 60, total % 60)


def ai_fill():
    """'Fill in my settings' button: ask the AI, then put its answers into the settings below."""
    import ai_prefs
    s = st.session_state
    s.ai_error, s.ai_summary, s.ai_changes = None, None, []
    text = (s.get("ai_text") or "").strip()
    if not text:
        s.ai_error = "Type what you're looking for first."
        return
    try:
        a = ai_prefs.understand(text)
    except Exception as e:
        s.ai_error = f"The AI couldn't help right now: {e}"
        return

    changes = []

    def put(key, value, label):
        if value is not None and value != "":
            s[key] = value
            changes.append(label)

    if a.get("trip_type"):
        put("t_type", "Round trip" if a["trip_type"] == "round_trip" else "One-way", f"Trip type: {a['trip_type'].replace('_', ' ')}")
    for field, key, label in [("origin", "t_origin", "From"), ("destination", "t_dest", "To")]:
        if a.get(field) and len(a[field]) == 3:
            put(key, a[field].upper(), f"{label}: {a[field].upper()}")
    for field, key, label in [("depart_date", "t_depart", "Leave"), ("return_date", "t_return", "Return")]:
        if a.get(field):
            try:
                put(key, date.fromisoformat(a[field]), f"{label}: {a[field]}")
            except ValueError:
                pass
    if s.t_return < s.t_depart:
        s.t_return = s.t_depart
    for field, key in [("depart_flex", "t_dflex"), ("return_flex", "t_rflex")]:
        if a.get(field) is not None:
            put(key, max(0, min(3, int(a[field]))), f"{field.split('_')[0].title()} date flexible ± {int(a[field])} days")
    if a.get("adults"):
        put("t_adults", max(1, min(9, int(a["adults"]))), f"Passengers: {a['adults']}")
    for field, key, label in [("max_price", "p_max_price", "Max price $"), ("price_leeway", "p_price_leeway", "Price leniency +$"),
                              ("target_price", "p_target_price", "Target price $"),
                              ("time_leeway_min", "p_time_leeway", "Travel time leniency (min) "),
                              ("window_leeway_min", "p_window_leeway", "Departure time leniency (min) ")]:
        if a.get(field) is not None:
            put(key, int(a[field]), f"{label}{int(a[field])}")
    if a.get("max_hours"):
        put("p_max_hours", float(round(a["max_hours"] * 2) / 2), f"Max travel time: {a['max_hours']}h")
    if a.get("stops"):
        put("p_stops", a["stops"], f"Stops: {a['stops']}")
    if a.get("depart_window_start") and a.get("depart_window_end"):
        s.p_use_windows = True
        put("p_out_window", (_hhmm(a["depart_window_start"]), _hhmm(a["depart_window_end"])),
            f"Leave between {a['depart_window_start']} and {a['depart_window_end']}")
    if a.get("return_window_start") and a.get("return_window_end"):
        s.p_use_windows = True
        put("p_back_window", (_hhmm(a["return_window_start"]), _hhmm(a["return_window_end"])),
            f"Return flight leaves between {a['return_window_start']} and {a['return_window_end']}")
    if a.get("use_windows") is False:
        put("p_use_windows", False, "Any departure time")
    if a.get("priority"):
        put("p_preset", a["priority"], f"What matters most: {a['priority']}")
        apply_preset()
    if a.get("allow_separate") is not None:
        put("p_separate", bool(a["allow_separate"]), "2 separate tickets: " + ("allowed" if a["allow_separate"] else "off"))
    s.ai_summary = a.get("summary", "")
    s.ai_unclear = a.get("unclear")
    s.ai_changes = changes


def current_prefs(round_trip):
    s = st.session_state
    return {
        **DEFAULT_PREFS,
        "max_price": s.p_max_price, "price_leeway": s.p_price_leeway, "target_price": s.p_target_price,
        "max_hours": s.p_max_hours, "time_leeway_min": s.p_time_leeway, "max_stops": STOPS[s.p_stops],
        "outbound_window": [t2s(x) for x in s.p_out_window] if s.p_use_windows else None,
        "return_window": [t2s(x) for x in s.p_back_window] if (s.p_use_windows and round_trip) else None,
        "window_leeway_min": s.p_window_leeway,
        "price_importance": s.p_price_imp, "time_importance": s.p_time_imp, "stops_importance": s.p_stops_imp,
        "allow_separate": s.p_separate, "great_score": s.p_great,
    }


# ---------- sidebar: usage + tracked trips ----------
with st.sidebar:
    st.subheader("Search budget")
    try:
        u = get_usage()
        st.progress(min(u["used"] / DEFAULT_MONTHLY_LIMIT, 1.0),
                    text=f"{u['used']} of {DEFAULT_MONTHLY_LIMIT} used this month")
        st.caption(f"SerpApi's count can lag a few minutes. Resets {u['renews']}.")
    except Exception:
        st.caption("Couldn't reach SerpApi to check usage.")
    st.caption(f"This session: {st.session_state.searches_session} searches")

    st.subheader("Tracked trips")
    wl = load_watchlist()
    if not wl["trips"]:
        st.caption("Nothing tracked yet. Search a trip, then press **Track this trip**.")
    for w in wl["trips"]:
        t = w["trip"]
        arrow = "⇄" if t.get("return_date") else "→"
        c1, c2 = st.columns([4, 1])
        c1.markdown(f"**{t['origin']} {arrow} {t['destination']}**  \n{t['depart_date']}"
                    + (f" – {t['return_date']}" if t.get("return_date") else "")
                    + f"  \nTarget ${w['prefs']['target_price']}")
        if c2.button("✕", key=f"rm_{w['id']}", help="Stop tracking"):
            wl["trips"] = [x for x in wl["trips"] if x["id"] != w["id"]]
            save_watchlist(wl)
            st.rerun()
    st.caption("Checks run every 3 hours, 6 AM–midnight ET. Reports at 9 AM and 6 PM.")

# ---------- main page ----------
st.title("✈️ Flight Scout")
st.caption("Real flight prices, scored for **you**, with the reasons why.")

st.subheader("1. Your trip")
trip_type = st.segmented_control("Trip type", ["Round trip", "One-way"], key="t_type",
                                 label_visibility="collapsed") or "Round trip"
round_trip = trip_type == "Round trip"
c1, c2 = st.columns(2)
origin = c1.text_input("From (airport code)", max_chars=3, key="t_origin").strip().upper()
destination = c2.text_input("To (airport code)", max_chars=3, key="t_dest").strip().upper()
c1, c2 = st.columns(2)
FLEX = [0, 1, 2, 3]
flex_label = lambda n: "exact date" if n == 0 else f"± {n} day{'s' if n > 1 else ''}"
c1a, c1b = c1.columns([3, 2])
depart = c1a.date_input("Leave", min_value=date.today(), key="t_depart")
c1b.selectbox("Flexible?", FLEX, key="t_dflex", format_func=flex_label)
if round_trip:
    c2a, c2b = c2.columns([3, 2])
    ret = c2a.date_input("Return", min_value=depart, key="t_return")
    c2b.selectbox("Flexible?", FLEX, key="t_rflex", format_func=flex_label)
else:
    ret = None
c1, c2 = st.columns(2)
adults = c1.number_input("Passengers", 1, 9, key="t_adults")
cabin = c2.selectbox("Cabin", ["economy", "premium economy", "business", "first"])

st.subheader("2. What you like")
tab_words, tab_custom = st.tabs(["✍️ Describe it", "🎛️ Set it myself"])

with tab_words:
    st.text_area("Describe your trip and what you like, in your own words",
                 placeholder="e.g. Boston to Miami Dec 20 for a week. I'm on a tight budget, around $400, "
                             "no red-eyes, and one short stop is fine.",
                 key="ai_text", height=110)
    st.button("✨ Fill in my settings", on_click=ai_fill, type="primary")
    st.caption("An AI model (Google Gemini) reads your words and fills in the settings. It costs no flight searches. "
               "The scoring itself stays rule-based, so every score still comes with reasons.")
    if st.session_state.get("ai_error"):
        st.error(st.session_state.ai_error)
    if st.session_state.get("ai_summary"):
        st.success(st.session_state.ai_summary)
        if st.session_state.get("ai_changes"):
            st.markdown("**What I set:** " + " · ".join(st.session_state.ai_changes))
        if st.session_state.get("ai_unclear"):
            st.info(f"Not sure about: {st.session_state.ai_unclear}")
        st.caption("Everything else stayed as it was. Check or tweak any of it in **Set it myself**.")

with tab_custom:
    st.markdown("**Price**")
    c1, c2, c3 = st.columns(3)
    c1.number_input("Max price ($, whole trip)", 50, 20000, step=25, key="p_max_price")
    c2.number_input("Leniency (+$)", 0, 5000, step=10, key="p_price_leeway",
                    help="How far over your max you'd still consider. Beyond this, trips are hidden.")
    c3.number_input("Target price ($)", 0, 20000, step=25, key="p_target_price",
                    help="At or below this, tracked trips send an urgent alert.")

    st.markdown("**Travel time (each direction)**")
    c1, c2 = st.columns(2)
    c1.number_input("Max hours", 1.0, 48.0, step=0.5, key="p_max_hours")
    c2.number_input("Leniency (+minutes)", 0, 600, step=15, key="p_time_leeway")

    st.markdown("**Stops**")
    st.segmented_control("Stops", list(STOPS), key="p_stops", label_visibility="collapsed")

    st.markdown("**Departure times**")
    st.toggle("Only certain departure times", key="p_use_windows")
    if st.session_state.p_use_windows:
        st.slider("Leave between", time(0), time(23, 30), step=timedelta(minutes=30),
                  key="p_out_window", format="h:mm a")
        if round_trip:
            st.slider("Return flight leaves between", time(0), time(23, 30), step=timedelta(minutes=30),
                      key="p_back_window", format="h:mm a")
        st.number_input("Leniency (± minutes)", 0, 240, step=15, key="p_window_leeway")

    st.markdown("**What matters most?**")
    st.segmented_control("Priority", list(PRIORITY_PRESETS), key="p_preset",
                         on_change=apply_preset, label_visibility="collapsed")
    with st.expander("Customize" + ("" if st.session_state.p_preset else " (custom mix)")):
        st.slider("Price", 1, 5, key="p_price_imp", on_change=sliders_changed)
        st.slider("Travel time", 1, 5, key="p_time_imp", on_change=sliders_changed)
        st.slider("Fewer stops", 1, 5, key="p_stops_imp", on_change=sliders_changed)
    split = category_points({"price_importance": st.session_state.p_price_imp,
                             "time_importance": st.session_state.p_time_imp,
                             "stops_importance": st.session_state.p_stops_imp})
    st.caption(f"Your mix: Price {split['price']:.0f}% · Time {split['time']:.0f}% · Stops {split['stops']:.0f}%")

    st.markdown("**More**")
    if round_trip:
        st.toggle("Allow 2 separate one-way tickets (can mix airlines)", key="p_separate",
                  help="Often cheaper, but if one flight is cancelled the other airline won't rebook you.")
    st.slider("Great-match score (urgent alert at or above)", 70, 100, key="p_great")

prefs = current_prefs(round_trip)
trip = {"depart_flex": st.session_state.t_dflex, "return_flex": st.session_state.t_rflex if round_trip else 0,
        "origin": origin, "destination": destination, "depart_date": depart.isoformat(),
        "return_date": ret.isoformat() if ret else None, "adults": int(adults), "cabin": cabin}

# ---------- search ----------
st.subheader("3. Search")
cost = scout.estimate_searches(trip)
ok = len(origin) == 3 and len(destination) == 3 and origin != destination
if st.button(f"🔎 Search flights  ·  uses {cost} search{'es' if cost > 1 else ''}", type="primary",
             disabled=not ok, use_container_width=True):
    before = flights.searches_this_run
    with st.spinner("Searching Google Flights and scoring every option..."):
        try:
            st.session_state.result = scout.find_trips(trip, prefs)
            st.session_state.result_id = uuid.uuid4().hex[:6]
            st.session_state.searched_trip = trip
        except BudgetError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Search failed: {e}")
    st.session_state.searches_session += flights.searches_this_run - before
    st.rerun()

result = st.session_state.get("result")
if not result:
    st.stop()

# Preferences changed since the search? Re-score for free with the flights we already have.
scout.rescore(result, current_prefs(result["round_trip"]))
rid = st.session_state.result_id
t = st.session_state.searched_trip
SHOW_DATES = bool(t.get("depart_flex") or t.get("return_flex"))

# ---------- results ----------
st.divider()
arrow = "⇄" if t.get("return_date") else "→"
st.subheader(f"{t['origin']} {arrow} {t['destination']}")
c1, c2 = st.columns([3, 1])
c1.caption(f"{len(result['trips'])} options fit your limits · {len(result['hidden'])} hidden (beyond your leniency) · "
           "change any setting above to re-score for free")
if c2.button("📌 Track this trip", use_container_width=True):
    wl = load_watchlist()
    wl["trips"].append({"id": f"{t['origin']}-{t['destination']}-{uuid.uuid4().hex[:6]}".lower(),
                        "active": True, "trip": t, "prefs": result["prefs"]})
    save_watchlist(wl)
    st.toast("Tracking! You'll get it in your reports and alerts.", icon="📌")
    st.rerun()

if not result["cards"]:
    st.warning(f"No trips fit your limits. {len(result['hidden'])} trips are outside them.")
    if result["hidden"] and st.toggle("Show trips outside my limits"):
        for h in result["hidden"][:15]:
            st.markdown(f"**${h['price']}** · {TICKET[h['ticket']]} · " + " · ".join(h["reasons"]))
    st.stop()

show_all = st.session_state.get(f"more_{rid}", False)
cards = scout.pick_top(result, len(result["cards"]) if show_all else scout.SHOW_TOP_WEB)


def returns_table(out_flight):
    rows = scout.returns_for_departure(result, out_flight)
    data = []
    for r in rows:
        tr = r["trip"]
        back = tr["legs"][1]
        vs = "best return" if r["is_best"] else scout._diff_text(r).replace(" vs best return", "")
        data.append({"Return": clock(back["depart"]), "Flight": leg(back), "Ticket": TICKET[tr["ticket"]],
                     "Score": tr["score"], "Price": f"${tr['price']}", "vs. best": vs})
    return pd.DataFrame(data)


for i, card in enumerate(cards, 1):
    with st.container(border=True):
        first = card["pairs"][0]
        top_line = f"#### {i}. {card['score']}/100 · ${card['price']}"
        st.markdown(top_line)
        st.caption(TICKET[card["ticket"]] + (f" · 💡 {card['note']}" if card["note"] else ""))

        outs = sorted({p["out"]["depart"]: p["out"] for p in card["pairs"]}.items())
        out_flights = [f for _, f in outs]
        unchecked = sorted(card.get("other_departures", []), key=lambda f: f["depart"])
        st.markdown(f"**Departure:** {day(first['out']['depart']) + ' · ' if SHOW_DATES else ''}{leg(first['out'])}")
        if card["round_trip"]:
            backs = sorted({r["depart"]: r for p in card["pairs"] for r in p["returns"]}.items())
            st.markdown(f"**Return:** {day(first['returns'][0]['depart']) + ' · ' if SHOW_DATES else ''}{leg(first['returns'][0])} — leaves "
                        + " or ".join(tclock(d) for d, _ in backs))
            # Tap a departure time to see its best returns
            options = {tclock(f["depart"]): f for f in out_flights}
            options.update({tclock(f["depart"]) + " *": f for f in unchecked})
            pick = st.pills("Departure times (tap one to see its returns)", list(options),
                            key=f"pick_{rid}_{i}", default=list(options)[0])
            if unchecked:
                st.caption("\\* round-trip ticket price not checked yet for these times (same listed price)")
            chosen = options.get(pick or list(options)[0])
            with st.expander(f"More returns for the {clock(chosen['depart'])} flight"):
                table = returns_table(chosen)
                limit = len(table) if st.checkbox("Show all", key=f"all_{rid}_{i}") else scout.MORE_RETURNS + 1
                st.dataframe(table.head(limit), hide_index=True, use_container_width=True)
                if not scout.round_trip_checked(result, chosen) and any(
                        scout._id(f) == scout._id(chosen) for f in result["rt_out"]):
                    if st.button(f"Check round-trip ticket prices for {clock(chosen['depart'])} · uses 1 search",
                                 key=f"chk_{rid}_{i}"):
                        try:
                            used = scout.check_round_trip_prices(result, chosen)
                            st.session_state.searches_session += used
                        except (BudgetError, ValueError) as e:
                            st.error(str(e))
                        st.rerun()
                elif scout.round_trip_checked(result, chosen):
                    st.caption("✓ Round-trip ticket prices checked for this time.")
        else:
            st.markdown("**Leaves at:** " + " or ".join(tclock(f["depart"]) for f in out_flights))

        with st.expander("Why this score?"):
            for r in card["reasons"]:
                st.markdown(f"- {r}")

if not show_all and len(result["cards"]) > len(cards):
    if st.button(f"Show all {len(result['cards'])} options"):
        st.session_state[f"more_{rid}"] = True
        st.rerun()
