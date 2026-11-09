"""Flight fetcher: asks SerpApi's Google Flights API for flights and simplifies the answer.

Every call to search_flights() costs 1 SerpApi search.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()  # reads SERPAPI_KEY from the hidden .env file (on your laptop)

SERPAPI_URL = "https://serpapi.com/search.json"
CABINS = {"economy": 1, "premium economy": 2, "business": 3, "first": 4}

# Counts how many searches THIS run of the program has made (shown in reports).
searches_this_run = 0


def _api_key():
    key = os.getenv("SERPAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("SERPAPI_KEY is missing. Put it in the .env file.")
    return key


def _simplify(option):
    """Turn one raw Google Flights result into a small, easy-to-read dictionary."""
    legs = option.get("flights", [])
    layovers = option.get("layovers", [])
    return {
        "price": option.get("price"),  # in USD; can be missing if Google has no price
        "duration_min": option.get("total_duration"),
        "stops": len(layovers),
        "layovers": [
            {
                "airport": l.get("id"),
                "minutes": l.get("duration"),
                "overnight": bool(l.get("overnight")),
            }
            for l in layovers
        ],
        "airlines": sorted({leg.get("airline", "?") for leg in legs}),
        "flight_numbers": [leg.get("flight_number", "") for leg in legs],
        "depart": legs[0]["departure_airport"]["time"] if legs else None,
        "arrive": legs[-1]["arrival_airport"]["time"] if legs else None,
        "from": legs[0]["departure_airport"]["id"] if legs else None,
        "to": legs[-1]["arrival_airport"]["id"] if legs else None,
        # Round trips only: this token is how we ask for return flights for this outbound flight.
        "departure_token": option.get("departure_token"),
        # Token for the Google Flights booking page (used for links later).
        "booking_token": option.get("booking_token"),
    }


def search_flights(origin, destination, depart_date, return_date=None,
                   adults=1, cabin="economy", departure_token=None):
    """Search flights. Costs 1 SerpApi search.

    - One-way: leave return_date as None.
    - Round trip: give return_date. Results are OUTBOUND flights, each with a departure_token.
    - Return flights: pass the same route/dates plus departure_token from a chosen outbound flight.
    Returns (flights, search_url) where flights is a list of simplified dictionaries.
    """
    params = {
        "engine": "google_flights",
        "departure_id": origin.upper(),
        "arrival_id": destination.upper(),
        "outbound_date": depart_date,
        "type": 1 if return_date else 2,  # 1 = round trip, 2 = one way
        "adults": adults,
        "travel_class": CABINS.get(cabin, 1),
        "currency": "USD",
        "hl": "en",
        "api_key": _api_key(),
    }
    if return_date:
        params["return_date"] = return_date
    if departure_token:
        params["departure_token"] = departure_token

    global searches_this_run
    searches_this_run += 1
    resp = requests.get(SERPAPI_URL, params=params, timeout=60)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"SerpApi said: {data['error']}")

    raw = data.get("best_flights", []) + data.get("other_flights", [])
    flights = [_simplify(o) for o in raw]
    google_url = data.get("search_metadata", {}).get("google_flights_url")
    return flights, google_url
