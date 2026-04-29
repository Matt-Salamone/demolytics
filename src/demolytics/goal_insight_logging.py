from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from demolytics.settings import get_app_data_dir

LOGGER = logging.getLogger(__name__)

_LOCK = threading.Lock()
_LOG_NAME = "goal_insights.jsonl"


def goal_insight_log_path() -> Path:
    return get_app_data_dir() / _LOG_NAME


def append_goal_insight_log(entry: dict[str, Any]) -> None:
    """Append one JSON object per line; survives across matches and app restarts."""
    path = goal_insight_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    LOGGER.debug("Appended goal insight log entry to %s", path)
