"""Plain-English preferences: an AI model (Google Gemini, free tier) turns what you type into settings.

Example:  "Boston to Miami Dec 20 for a week, I'm broke, no red-eyes, one short stop is fine"
   ->  origin BOS, destination MIA, dates, max price, departure window, up to 1 stop, "Cheapest"...

The AI only FILLS IN SETTINGS. The scoring itself stays rule-based, so results are predictable
and every score still comes with reasons. ("AI handles the language, rules make the decisions.")
Needs GEMINI_API_KEY in .env (free from aistudio.google.com). Costs 0 SerpApi searches.
"""
import json
import os
import re
from datetime import date

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://generativelanguage.googleapis.com/v1beta"

# What the AI must answer with. null = "you didn't mention it, keep the current setting".
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "origin": {"type": "STRING", "nullable": True, "description": "3-letter IATA airport code"},
        "destination": {"type": "STRING", "nullable": True, "description": "3-letter IATA airport code"},
        "trip_type": {"type": "STRING", "enum": ["round_trip", "one_way"], "nullable": True},
        "depart_date": {"type": "STRING", "nullable": True, "description": "YYYY-MM-DD"},
        "return_date": {"type": "STRING", "nullable": True, "description": "YYYY-MM-DD"},
        "adults": {"type": "INTEGER", "nullable": True},
        "depart_flex": {"type": "INTEGER", "nullable": True, "description": "0-3 days of flexibility on departure date"},
        "return_flex": {"type": "INTEGER", "nullable": True, "description": "0-3 days of flexibility on return date"},
        "max_price": {"type": "INTEGER", "nullable": True, "description": "USD for the whole trip"},
        "price_leeway": {"type": "INTEGER", "nullable": True, "description": "USD over max still acceptable"},
        "target_price": {"type": "INTEGER", "nullable": True, "description": "USD 'great deal' price"},
        "max_hours": {"type": "NUMBER", "nullable": True, "description": "max travel hours per direction"},
        "time_leeway_min": {"type": "INTEGER", "nullable": True},
        "stops": {"type": "STRING", "enum": ["Nonstop only", "Up to 1 stop", "Any number"], "nullable": True},
        "use_windows": {"type": "BOOLEAN", "nullable": True},
        "depart_window_start": {"type": "STRING", "nullable": True, "description": "HH:MM 24h"},
        "depart_window_end": {"type": "STRING", "nullable": True, "description": "HH:MM 24h"},
        "return_window_start": {"type": "STRING", "nullable": True, "description": "HH:MM 24h"},
        "return_window_end": {"type": "STRING", "nullable": True, "description": "HH:MM 24h"},
        "window_leeway_min": {"type": "INTEGER", "nullable": True},
        "priority": {"type": "STRING", "enum": ["Balanced", "Cheapest", "Fastest", "Fewest stops"], "nullable": True},
        "allow_separate": {"type": "BOOLEAN", "nullable": True},
        "summary": {"type": "STRING", "description": "1-2 friendly sentences: what you set and why"},
        "unclear": {"type": "STRING", "nullable": True, "description": "anything you couldn't understand"},
    },
    "required": ["summary"],
}

INSTRUCTIONS = """You turn a traveler's plain-English description into settings for a flight search tool.
Today is {today}. Rules:
- Only fill a field if the person said or clearly implied it. Otherwise use null (the current setting is kept).
- Airports: use the main 3-letter IATA code (New York -> JFK, Boston -> BOS, Los Angeles -> LAX,
  San Francisco -> SFO, Chicago -> ORD, Miami -> MIA, Washington DC -> DCA, Durham/Raleigh -> RDU).
- Dates: interpret relative dates from today; "for a week" means return 7 days after departure.
  If a date has no year, pick the next upcoming one.
- "Flexible by a day or two"/"around Dec 20" -> depart_flex (and return_flex for round trips) 1 or 2.
  "Flexible on the way back" -> only return_flex. Max 3. Exact dates or nothing said -> null.
- "Red-eye"/"no overnight flights" -> use_windows true and a departure window like 06:00-21:00.
  "Morning person" -> e.g. 06:00-12:00; "not too early" -> starts 08:00 or later.
- Budget words: "broke"/"tight budget"/"cheap" -> priority Cheapest; a dollar amount is max_price.
  If they give a max but no target, set target_price about 15% below max. A "little wiggle room" ~ 10% of max.
- "Hate layovers"/"direct" -> Nonstop only (or "Fewest stops" priority if they sound flexible).
  "One short stop is fine" -> Up to 1 stop.
- "Fast"/"in a hurry" -> priority Fastest.
- "Separate tickets"/"mix airlines" -> allow_separate true; "keep it simple"/"one ticket" -> false.
Write the summary to the traveler in a friendly tone, mentioning each setting you chose."""


def _key():
    load_dotenv()  # re-read .env so a newly added key works without restarting the app
    k = os.getenv("GEMINI_API_KEY", "").strip()
    if not k:
        raise RuntimeError("GEMINI_API_KEY is missing. Get a free key at aistudio.google.com and add it to .env.")
    return k


_model_cache = {}
FAST_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]


def model_candidates():
    """Fast text models this key can use, best first: Google's 'latest flash' alias, then the newest
    numbered flash models. (Google retires old models, so we never hard-code just one.)"""
    if "list" in _model_cache:
        return _model_cache["list"]
    models = requests.get(f"{API}/models", params={"key": _key(), "pageSize": 200}, timeout=30).json()
    if "error" in models:
        raise RuntimeError(f"Gemini said: {models['error'].get('message')}")
    usable = {m["name"].split("/")[-1] for m in models.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])}
    numbered = sorted((m for m in usable if re.fullmatch(r"gemini-\d+(\.\d+)?-flash", m)),
                      key=lambda m: float(m.split("-")[1]), reverse=True)
    fast = [m for m in FAST_MODELS if m in usable]  # measured: answers in a few seconds
    ordered = fast + [m for m in numbered if m not in fast]
    if not ordered:
        raise RuntimeError("No suitable Gemini model found for this key.")
    _model_cache["list"] = ordered
    return ordered


def understand(text, today=None):
    """Send the description to Gemini. Returns a dict of settings (null = not mentioned) + 'summary'."""
    today = today or date.today()
    body = {
        "systemInstruction": {"parts": [{"text": INSTRUCTIONS.format(today=today.strftime("%A, %B %-d, %Y"))}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0.2},
    }
    last_error = None
    for model in model_candidates()[:4]:  # if Google retired one, try the next
        try:
            data = requests.post(f"{API}/models/{model}:generateContent",
                                 params={"key": _key()}, json=body, timeout=20).json()
        except requests.RequestException as e:  # too slow or network hiccup -> try the next model
            last_error = f"{model} didn't answer in time"
            continue
        if "error" in data:
            last_error = data["error"].get("message")
            continue
        _model_cache["list"] = [model] + [m for m in _model_cache["list"] if m != model]  # remember what worked
        raw = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw)
    raise RuntimeError(f"Gemini said: {last_error}")
