"""Background GitHub release version check (non-blocking)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from packaging.version import InvalidVersion, Version

from demolytics import __version__

LOGGER = logging.getLogger(__name__)

GITHUB_OWNER = "Matt-Salamone"
GITHUB_REPO = "Demolytics"

CURRENT_VERSION: str = __version__

_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def normalize_release_tag(tag_name: str) -> str:
    """Strip a leading 'v' from release tags (e.g. v1.1.0 -> 1.1.0)."""
    s = tag_name.strip()
    if len(s) > 1 and s[0].lower() == "v" and (s[1].isdigit() or s[1] == "."):
        return s[1:]
    return s


@dataclass(frozen=True)
class LatestReleaseInfo:
    tag_name: str
    html_url: str
    display_version: str


def fetch_latest_release_info(*, timeout_s: float = 10.0) -> LatestReleaseInfo | None:
    """
    GET latest release from GitHub. Returns None on network/parsing errors (logged at debug).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = requests.get(_LATEST_RELEASE_URL, headers=headers, timeout=timeout_s)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        LOGGER.debug("Version check request failed: %s", exc)
        return None
    except ValueError as exc:
        LOGGER.debug("Version check JSON decode failed: %s", exc)
        return None

    try:
        tag = data["tag_name"]
        html_url = data["html_url"]
    except (KeyError, TypeError) as exc:
        LOGGER.debug("Version check unexpected payload: %s", exc)
        return None

    if not isinstance(tag, str) or not isinstance(html_url, str):
        return None

    return LatestReleaseInfo(
        tag_name=tag,
        html_url=html_url,
        display_version=normalize_release_tag(tag),
    )


def is_remote_newer(remote_display: str, current: str) -> bool:
    """True if remote semantic version is strictly greater than current."""
    try:
        return Version(remote_display) > Version(current)
    except InvalidVersion:
        return False


def remote_is_newer_than_current(info: LatestReleaseInfo, current: str = CURRENT_VERSION) -> bool:
    return is_remote_newer(info.display_version, current)
