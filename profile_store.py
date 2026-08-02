"""
profile_store.py

Persists SPS's Company Profile as a plain JSON file (data/company_profile.json)
instead of it only living in Streamlit's in-memory session_state — which
resets to checklist_items.DEFAULT_COMPANY_PROFILE every time the app
restarts — or requiring a developer to edit DEFAULT_COMPANY_PROFILE in code
every time something about the company changes.

WHY A JSON FILE (not a database): this is a single record, edited rarely, by
one team. A whole database table for one row would be solving a problem this
doesn't have — same reasoning as history_store.py choosing SQLite over a
server, just one step lighter since there's no list/history involved here,
just one current profile.

DEFAULT_COMPANY_PROFILE is the SEED/fallback only. Once someone saves an
edit through the sidebar, company_profile.json becomes the source of truth.
If a future code update adds a new profile field, load_profile() still fills
it in from the default so old saved files don't break — only overwrites
fields, doesn't require the saved file to have every key.
"""

import json
import os

from checklist_items import DEFAULT_COMPANY_PROFILE

PROFILE_PATH = os.path.join(os.path.dirname(__file__), "data", "company_profile.json")


def load_profile() -> dict:
    """Returns the saved profile if one exists, seeded with any fields the
    saved file is missing (so adding a new field to DEFAULT_COMPANY_PROFILE
    later doesn't crash on an old saved file). Falls back to the code
    defaults entirely if no file has been saved yet, or if it's unreadable."""
    profile = dict(DEFAULT_COMPANY_PROFILE)
    if os.path.exists(PROFILE_PATH):
        try:
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                profile.update(saved)
        except (json.JSONDecodeError, OSError):
            pass  # corrupted/unreadable file -> fall back to defaults rather than crash the app
    return profile


def save_profile(profile: dict):
    os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


def is_saved() -> bool:
    """Whether a profile has ever been saved (vs. still running on code
    defaults) — used to show a small status hint in the sidebar."""
    return os.path.exists(PROFILE_PATH)
