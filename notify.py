"""Sends notifications to your phone through ntfy (free, no account).

Your phone subscribes to a private channel name (NTFY_TOPIC in .env).
Anything sent to that channel pops up on your phone.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

NTFY_SERVER = "https://ntfy.sh"


def send(title, message, urgent=False, tags=None, click_url=None):
    """Send one notification. urgent=True makes it a loud, high-priority alert."""
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        raise RuntimeError("NTFY_TOPIC is missing. Put it in the .env file.")
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 5 if urgent else 3,   # 5 = urgent (loud), 3 = normal
        "tags": tags or [],
    }
    if click_url:
        payload["click"] = click_url      # tapping the notification opens this link
    resp = requests.post(NTFY_SERVER, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("id")


if __name__ == "__main__":
    send("Flight Scout is connected ✈️",
         "If you can read this, phone notifications work!", tags=["airplane"])
    print("Test notification sent.")
