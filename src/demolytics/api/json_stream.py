from __future__ import annotations

import json


class JsonStreamSplitter:
    """Incrementally split a byte stream into JSON text documents (Stats API over TCP)."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: bytes) -> list[str]:
        self._buf += data.decode("utf-8", errors="replace")
        out: list[str] = []
        decoder = json.JSONDecoder()
        while True:
            stripped = self._buf.lstrip()
            if not stripped:
                self._buf = ""
                break
            try:
                _obj, end = decoder.raw_decode(stripped)
            except json.JSONDecodeError:
                self._buf = stripped
                break
            out.append(stripped[:end])
            self._buf = stripped[end:]
        return out
