"""
calendar_link.py

Builds an "Add to Google Calendar" link for an RFP's extracted submission
deadline. Deliberately just a URL — no Google API, no OAuth, nothing to
configure or that can fail with an auth error. Clicking it opens Google
Calendar's own "create event" screen pre-filled with the deadline; the
person still reviews and confirms before anything is actually added to
their calendar. That's the entire integration.
"""

from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote


def build_google_calendar_link(title: str, date_iso: Optional[str], details: str = "") -> Optional[str]:
    """
    date_iso: the RFP's extracted deadline, e.g. "2026-09-01" (date only) or
    "2026-09-01T17:00:00" (date + time). Returns None if date_iso is missing
    or unparseable — callers should just skip showing the button in that
    case rather than showing a broken link.
    """
    if not date_iso or not date_iso.strip():
        return None

    raw = date_iso.strip()
    dt = None
    try:
        # fromisoformat historically rejected a trailing "Z" pre-Python 3.11;
        # normalize it so this works across Python versions.
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return None

    date_only = len(raw) <= 10
    if date_only:
        # All-day event. Google's all-day date range end is EXCLUSIVE, so a
        # one-day event needs end = start + 1 day, or Google shows it as
        # spanning zero days.
        start = dt.strftime("%Y%m%d")
        end = (dt + timedelta(days=1)).strftime("%Y%m%d")
    else:
        # Zero-duration marker exactly at the deadline instant, rather than
        # guessing a plausible duration for something that isn't a meeting.
        start = dt.strftime("%Y%m%dT%H%M%S")
        end = start

    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{start}/{end}",
        "details": details,
    }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"https://calendar.google.com/calendar/render?{query}"
