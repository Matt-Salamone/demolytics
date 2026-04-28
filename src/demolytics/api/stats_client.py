from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable
from queue import Queue
from typing import Any

from demolytics.domain.events import StatsEvent, parse_message

LOGGER = logging.getLogger(__name__)
EventListener = Callable[[StatsEvent], None | Awaitable[None]]
StatusListener = Callable[[str], None]


class StatsApiClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 49123,
        reconnect_delay_seconds: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.reconnect_delay_seconds = reconnect_delay_seconds
        self._event_listeners: list[EventListener] = []
        self._status_listeners: list[StatusListener] = []
        self._stop_requested = False

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def add_event_listener(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def add_status_listener(self, listener: StatusListener) -> None:
        self._status_listeners.append(listener)

    def request_stop(self) -> None:
        self._stop_requested = True

    async def run_forever(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            self._emit_status("websockets package is not installed")
            raise RuntimeError("Install the websockets package to use live ingestion.") from exc

        self._stop_requested = False
        while not self._stop_requested:
            try:
                self._emit_status(f"connecting to {self.uri}")
                async with websockets.connect(self.uri) as websocket:
                    self._emit_status("connected")
                    async for raw_message in websocket:
                        if self._stop_requested:
                            break
                        await self._handle_raw_message(raw_message)
            except asyncio.CancelledError:
                self._emit_status("stopped")
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect loop must survive API downtime.
                LOGGER.debug("Stats API connection failed: %s", exc)
                self._emit_status("waiting for Rocket League Stats API")
                await asyncio.sleep(self.reconnect_delay_seconds)
        self._emit_status("stopped")

    async def _handle_raw_message(self, raw_message: str | bytes) -> None:
        try:
            event = parse_message(raw_message)
        except (ValueError, TypeError) as exc:
            LOGGER.warning("Ignoring malformed Stats API message: %s", exc)
            return

        for listener in self._event_listeners:
            result = listener(event)
            if inspect.isawaitable(result):
                await result

    def _emit_status(self, status: str) -> None:
        for listener in self._status_listeners:
            listener(status)


class StatsApiThread:
    """Runs the async WebSocket client without blocking Tk's main loop."""

    def __init__(self, client: StatsApiClient, event_queue: Queue[StatsEvent]) -> None:
        self.client = client
        self.event_queue = event_queue
        self.status_queue: Queue[str] = Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.client.add_event_listener(self.event_queue.put)
        self.client.add_status_listener(self.status_queue.put)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.client.request_stop()

    def _run(self) -> None:
        try:
            asyncio.run(self.client.run_forever())
        except Exception as exc:  # noqa: BLE001 - surface background failure to UI.
            LOGGER.exception("Stats API thread crashed")
            self.status_queue.put(f"error: {exc}")


def drain_queue(queue: Queue[Any]) -> list[Any]:
    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items
