from __future__ import annotations

import asyncio
import inspect
import logging
import socket
import threading
from contextlib import suppress
from collections.abc import Awaitable, Callable
from queue import Queue
from typing import Any

from demolytics.api.json_stream import JsonStreamSplitter
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
        self._plain_tcp_mode = False

    @property
    def uri(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def add_event_listener(self, listener: EventListener) -> None:
        self._event_listeners.append(listener)

    def add_status_listener(self, listener: StatusListener) -> None:
        self._status_listeners.append(listener)

    def request_stop(self) -> None:
        self._stop_requested = True

    async def _run_plain_tcp_session(self) -> None:
        LOGGER.debug("Stats API: opening TCP JSON stream to %s:%s", self.host, self.port)
        reader, writer = await asyncio.open_connection(
            self.host,
            self.port,
            family=socket.AF_INET,
        )
        self._emit_status("connected")
        splitter = JsonStreamSplitter()
        try:
            while not self._stop_requested:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=90.0)
                if not chunk:
                    break
                for raw in splitter.feed(chunk):
                    await self._handle_raw_message(raw)
        except TimeoutError:
            LOGGER.debug("Stats API TCP read idle timeout; reconnecting.")
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    async def run_forever(self) -> None:
        try:
            import websockets
            from websockets.exceptions import InvalidMessage
        except ImportError as exc:
            self._emit_status("websockets package is not installed")
            raise RuntimeError("Install the websockets package to use live ingestion.") from exc

        self._stop_requested = False
        while not self._stop_requested:
            if self._plain_tcp_mode:
                try:
                    await self._run_plain_tcp_session()
                except asyncio.CancelledError:
                    self._emit_status("stopped")
                    raise
                except Exception as exc:  # noqa: BLE001 - reconnect loop must survive API downtime.
                    LOGGER.debug("Stats API TCP session failed: %s", exc)
                    self._emit_status("waiting for Rocket League Stats API")
                await asyncio.sleep(self.reconnect_delay_seconds)
                continue

            try:
                LOGGER.debug("Stats API: opening WebSocket %s", self.uri)
                # Never use a system HTTP/SOCKS proxy for loopback. Some embedded WS
                # stacks expect Origin; AF_INET avoids odd dual-stack loopback paths.
                async with websockets.connect(
                    self.uri,
                    proxy=None,
                    compression=None,
                    user_agent_header=None,
                    origin="http://127.0.0.1",
                    family=socket.AF_INET,
                ) as websocket:
                    self._emit_status("connected")
                    async for raw_message in websocket:
                        if self._stop_requested:
                            break
                        await self._handle_raw_message(raw_message)
            except asyncio.CancelledError:
                self._emit_status("stopped")
                raise
            except InvalidMessage as exc:
                LOGGER.debug("Stats API WebSocket invalid HTTP response: %s", exc)
                if not self._plain_tcp_mode:
                    self._plain_tcp_mode = True
                self._emit_status("Stats API: using TCP JSON stream")
                await asyncio.sleep(0.5)
                continue
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
