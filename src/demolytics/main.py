from __future__ import annotations

from demolytics.db.repository import DemolyticsRepository
from demolytics.logging_config import configure_logging
from demolytics.settings import load_settings
from demolytics.ui.app import DemolyticsApp


def main() -> None:
    configure_logging()
    settings = load_settings()
    repository = DemolyticsRepository(settings.database_path or "")
    repository.initialize()
    app = DemolyticsApp(settings=settings, repository=repository)
    app.mainloop()


if __name__ == "__main__":
    main()
