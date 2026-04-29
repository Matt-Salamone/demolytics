from __future__ import annotations

import json
import unittest

from demolytics.api.json_stream import JsonStreamSplitter


class JsonStreamSplitterTests(unittest.TestCase):
    def test_single_object_one_chunk(self) -> None:
        payload = {"Event": "UpdateState", "Data": {"MatchGuid": "x"}}
        raw = json.dumps(payload)
        splitter = JsonStreamSplitter()
        parts = splitter.feed(raw.encode("utf-8"))
        self.assertEqual(len(parts), 1)
        self.assertEqual(json.loads(parts[0]), payload)

    def test_two_objects_split_across_chunks(self) -> None:
        a = json.dumps({"Event": "A", "Data": {}})
        b = json.dumps({"Event": "B", "Data": {}})
        splitter = JsonStreamSplitter()
        mid = len(a) // 2
        parts1 = splitter.feed((a[:mid]).encode())
        parts2 = splitter.feed((a[mid:] + b).encode())
        self.assertEqual(parts1, [])
        self.assertEqual(len(parts2), 2)
        self.assertEqual(json.loads(parts2[0])["Event"], "A")
        self.assertEqual(json.loads(parts2[1])["Event"], "B")


if __name__ == "__main__":
    unittest.main()
