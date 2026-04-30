from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from demolytics.version_check import (
    LatestReleaseInfo,
    fetch_latest_release_info,
    is_remote_newer,
    normalize_release_tag,
    remote_is_newer_than_current,
)


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.1.0", "1.1.0"),
        ("V2.0.0", "2.0.0"),
        ("v0.1.0-rc1", "0.1.0-rc1"),
        ("1.2.3", "1.2.3"),
        ("stable", "stable"),
    ],
)
def test_normalize_release_tag(tag: str, expected: str) -> None:
    assert normalize_release_tag(tag) == expected


def test_is_remote_newer() -> None:
    assert is_remote_newer("1.1.0", "1.0.0") is True
    assert is_remote_newer("1.0.0", "1.0.0") is False
    assert is_remote_newer("0.1.0", "1.0.0") is False


def test_remote_is_newer_than_current_uses_display_version() -> None:
    info = LatestReleaseInfo(tag_name="v2.0.0", html_url="https://x", display_version="2.0.0")
    assert remote_is_newer_than_current(info, "1.0.0") is True
    assert remote_is_newer_than_current(info, "2.0.0") is False


@patch("demolytics.version_check.requests.get")
def test_fetch_latest_release_info_success(mock_get: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v1.2.3",
        "html_url": "https://github.com/o/r/releases/tag/v1.2.3",
    }
    mock_get.return_value = mock_response

    info = fetch_latest_release_info(timeout_s=5.0)
    assert info is not None
    assert info.display_version == "1.2.3"
    assert info.html_url.startswith("https://github.com")


@patch("demolytics.version_check.requests.get")
def test_fetch_latest_release_info_network_error(mock_get: MagicMock) -> None:
    import requests

    mock_get.side_effect = requests.exceptions.ConnectionError("offline")
    assert fetch_latest_release_info() is None
