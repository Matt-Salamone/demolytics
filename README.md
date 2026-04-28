# Demolytics

Demolytics is a Windows desktop companion for Rocket League. It reads the official local Rocket League Stats API WebSocket, displays live session stats, and stores finished match data in SQLite for historical comparisons.

## Development

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest
```

Run the app with:

```bash
demolytics
```

Rocket League must have `TAGame\Config\DefaultStatsAPI.ini` configured with `PacketSendRate > 0` before live data is available.
