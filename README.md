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
# or
.venv/Scripts/python -m demolytics.main
```

Rocket League must have `TAGame\Config\DefaultStatsAPI.ini` configured with `PacketSendRate > 0` before live data is available.

## Test builds (no Python required)

GitHub Actions publishes a Windows zip for each [release](https://github.com/Matt-Salamone/demolytics/releases). Download `Demolytics-v*-Windows.zip`, extract it, and run `Demolytics.exe`.

To cut a release from your machine: commit your work, then create and push a version tag (must match `v*`). The workflow builds the app and attaches the zip to the release.

```bash
git tag v0.1.0
git push origin v0.1.0
```

Requires GitHub Actions enabled on the repo. For a one-off local build: `pip install -e ".[build]"` then `pyinstaller Demolytics.spec`; output is under `dist/Demolytics/`.