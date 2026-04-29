from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from demolytics.integrations.ballchasing import BallchasingUploadError, upload_replay_file


class BallchasingUploadTests(unittest.TestCase):
    def test_upload_replay_201_returns_id(self) -> None:
        replay = Path(tempfile.gettempdir()) / "demolytics_test_ballchasing.replay"
        replay.write_bytes(b"replay-bytes-test")
        try:

            class FakeResp:
                status = 201

                def read(self) -> bytes:
                    return b'{"id":"replay-id-201","location":"https://ballchasing.com/replay/replay-id-201"}'

                def __enter__(self) -> FakeResp:
                    return self

                def __exit__(self, *args: object) -> None:
                    return None

            captured: dict[str, object] = {}

            def fake_urlopen(request: object, timeout: float = 0.0) -> FakeResp:
                captured["data"] = getattr(request, "data", None)
                captured["headers"] = dict(getattr(request, "header_items", lambda: [])())
                return FakeResp()

            with patch("demolytics.integrations.ballchasing.urllib.request.urlopen", side_effect=fake_urlopen):
                rid = upload_replay_file(replay, "test-token", "private")

            self.assertEqual(rid, "replay-id-201")
            body = captured["data"]
            assert isinstance(body, bytes)
            self.assertIn(b'name="file"', body)
            self.assertIn(b"replay-bytes-test", body)
            headers = captured["headers"]
            self.assertEqual(headers.get("Authorization"), "test-token")
            self.assertIn("multipart/form-data", headers.get("Content-type", ""))
        finally:
            replay.unlink(missing_ok=True)

    def test_upload_replay_409_duplicate_returns_id(self) -> None:
        replay = Path(tempfile.gettempdir()) / "demolytics_test_ballchasing_409.replay"
        replay.write_bytes(b"x")
        try:

            def fake_urlopen(request: object, timeout: float = 0.0) -> FakeResp:
                raise urllib.error.HTTPError(
                    "https://ballchasing.com/api/v2/upload",
                    409,
                    "Conflict",
                    hdrs={},
                    fp=io.BytesIO(b'{"id":"existing-id","error":"duplicate replay"}'),
                )

            with patch("demolytics.integrations.ballchasing.urllib.request.urlopen", side_effect=fake_urlopen):
                rid = upload_replay_file(replay, "tok", "public")

            self.assertEqual(rid, "existing-id")
        finally:
            replay.unlink(missing_ok=True)

    def test_upload_missing_token_raises(self) -> None:
        replay = Path(tempfile.gettempdir()) / "demolytics_test_ballchasing_empty.replay"
        replay.write_bytes(b"x")
        try:
            with self.assertRaises(BallchasingUploadError):
                upload_replay_file(replay, "   ", "private")
        finally:
            replay.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
