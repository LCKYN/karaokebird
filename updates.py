"""Anonymous check for a newer GitHub release (no Qt)."""

import json
import re
import urllib.request

LATEST_RELEASE_API = (
    "https://api.github.com/repos/joshshiman/karaokebird/releases/latest"
)
CHECK_INTERVAL_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 5


def parse_version(text):
    """Turn a tag like "v1.2.3" or "1.2.3.0" into (1, 2, 3); None if there's
    no version number. Trailing zeros are dropped so "1.2" == "1.2.0.0"."""
    match = re.search(r"\d+(?:\.\d+)*", text or "")
    if not match:
        return None
    parts = [int(p) for p in match.group(0).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(latest, current):
    latest_v, current_v = parse_version(latest), parse_version(current)
    if latest_v is None or current_v is None:
        return False
    return latest_v > current_v


def is_check_due(last_check, now, interval=CHECK_INTERVAL_SECONDS):
    return now - last_check >= interval


def fetch_latest_release(url=LATEST_RELEASE_API, timeout=TIMEOUT_SECONDS):
    """Blocking GET of the latest release. Returns ``(tag, html_url)``.

    Sends no identifying data beyond what any HTTPS request carries.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "KaraokeBird-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    return data["tag_name"], data.get("html_url", "")
