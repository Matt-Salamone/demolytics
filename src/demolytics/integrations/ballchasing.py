from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)

BALLCHASING_UPLOAD_BASE = "https://ballchasing.com/api/v2/upload"


class BallchasingUploadError(Exception):
    """Raised when the Ballchasing API returns an error or an unexpected response."""


def _log_ballchasing_upload_response(http_status: int, raw: str) -> str:
    """Parse JSON, log id/location (and error for duplicates), return replay id."""
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        LOGGER.warning(
            "Ballchasing upload HTTP %s returned non-JSON body (first 500 chars): %r",
            http_status,
            raw[:500],
        )
        raise BallchasingUploadError(f"Invalid JSON from Ballchasing: {raw[:500]}") from None

    replay_id = payload.get("id")
    if not isinstance(replay_id, str) or not replay_id:
        LOGGER.warning(
            "Ballchasing upload HTTP %s JSON missing id: %s",
            http_status,
            json.dumps(payload, default=str)[:800],
        )
        raise BallchasingUploadError(f"Missing replay id in response: {raw[:500]}")

    location = payload.get("location")
    err = payload.get("error")
    LOGGER.info(
        "Ballchasing upload HTTP %s ok: replay_id=%s location=%s%s",
        http_status,
        replay_id,
        location if isinstance(location, str) else None,
        f" error={err!r}" if err else "",
    )
    LOGGER.debug("Ballchasing upload full response JSON: %s", json.dumps(payload, default=str))
    return replay_id


def upload_replay_file(
    path: Path,
    token: str,
    visibility: str,
    *,
    timeout: float = 120.0,
) -> str:
    """
    POST replay file to Ballchasing. Returns replay id.
    Treats HTTP 201 and 409 (duplicate) as success per API docs.
    """
    token = token.strip()
    if not token:
        raise BallchasingUploadError("Missing Ballchasing API token.")

    file_bytes = path.read_bytes()
    filename = path.name.replace('"', "")
    boundary = f"demolytics-{uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n".encode("utf-8")
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )

    query = urllib.parse.urlencode({"visibility": visibility})
    url = f"{BALLCHASING_UPLOAD_BASE}?{query}"

    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(2.0 * attempt)

        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": token,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if response.status not in (200, 201):
                    LOGGER.warning(
                        "Ballchasing upload unexpected HTTP %s body (first 500 chars): %r",
                        response.status,
                        raw[:500],
                    )
                    raise BallchasingUploadError(f"Unexpected status {response.status}: {raw[:500]}")
                return _log_ballchasing_upload_response(response.status, raw)
        except urllib.error.HTTPError as exc:
            body_txt = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code == 409:
                return _log_ballchasing_upload_response(exc.code, body_txt)
            if exc.code == 429 and attempt < 2:
                LOGGER.debug("Ballchasing rate limited (429), retrying: %s", body_txt[:200])
                last_exc = exc
                continue
            LOGGER.warning(
                "Ballchasing upload HTTP %s body (first 500 chars): %r",
                exc.code,
                body_txt[:500],
            )
            raise BallchasingUploadError(f"HTTP {exc.code}: {body_txt[:500] or exc.reason}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < 2:
                LOGGER.debug("Ballchasing upload network error, retrying: %s", exc)
                continue
            raise BallchasingUploadError(f"Network error: {exc}") from exc

    if last_exc:
        raise BallchasingUploadError(f"Upload failed after retries: {last_exc}") from last_exc
    raise BallchasingUploadError("Upload failed.")
