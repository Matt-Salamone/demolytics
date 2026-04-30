from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from demolytics.db.repository import DemolyticsRepository
from demolytics.logging_config import configure_logging
from demolytics.settings import load_settings
from demolytics.setup.stats_api import _patch_default_stats_api_ini
from demolytics.ui.app import DemolyticsApp

_LOGGER = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--elevated-patch-ini", dest="elevated_patch_ini", default=None)
    args, _unknown = parser.parse_known_args()

    if args.elevated_patch_ini:
        configure_logging()
        try:
            _patch_default_stats_api_ini(Path(args.elevated_patch_ini))
        except OSError as exc:
            _LOGGER.error("Elevated Stats API INI patch failed: %s", exc)
            sys.exit(1)
        sys.exit(0)

    configure_logging()
    settings = load_settings()
    repository = DemolyticsRepository(settings.database_path or "")
    repository.initialize()
    app = DemolyticsApp(settings=settings, repository=repository)
    app.mainloop()


if __name__ == "__main__":
    main()
