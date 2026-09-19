"""The agent's offline buffer must not replay undeliverable reports forever."""
import json
import pathlib
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "agent"))
import agent  # noqa: E402


def _resp(status, detail="x"):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"detail": detail}
    r.text = detail
    r.reason_phrase = "reason"
    r.request = MagicMock()
    return r


@pytest.fixture
def buffer_file(tmp_path, monkeypatch):
    f = tmp_path / "telemetry_buffer.json"
    monkeypatch.setattr(agent, "BUFFER_FILE", f)
    return f


def _write(buffer_file, items):
    buffer_file.write_text(json.dumps(items))


def test_reports_from_a_previous_device_are_dropped_without_a_request(buffer_file):
    _write(buffer_file, [{"device_id": "old-device", "latency_ms": 1}] * 3)
    with patch("agent.httpx.post") as post:
        agent.flush_buffer("current-device")
    post.assert_not_called()
    assert not buffer_file.exists()


def test_server_rejected_data_is_dropped(buffer_file):
    _write(buffer_file, [{"device_id": "current-device", "latency_ms": 1}])
    with patch("agent.httpx.post", return_value=_resp(422, "Telemetry rejected: fk")):
        agent.flush_buffer("current-device")
    assert not buffer_file.exists()


def test_outage_is_retried_then_given_up(buffer_file):
    _write(buffer_file, [{"device_id": "current-device", "latency_ms": 1}])
    with patch("agent.httpx.post", return_value=_resp(503, "down")):
        for _ in range(agent.MAX_BUFFER_RETRIES):
            agent.flush_buffer("current-device")
            assert buffer_file.exists(), "should keep retrying while under the limit"
        agent.flush_buffer("current-device")
    assert not buffer_file.exists(), "dropped after exceeding MAX_BUFFER_RETRIES"


def test_successful_replay_clears_buffer(buffer_file):
    _write(buffer_file, [{"device_id": "current-device", "latency_ms": 1}] * 2)
    with patch("agent.httpx.post", return_value=_resp(200)) as post:
        agent.flush_buffer("current-device")
    assert post.call_count == 2
    assert not buffer_file.exists()


def test_corrupt_buffer_is_discarded(buffer_file):
    buffer_file.write_text("{not json")
    with patch("agent.httpx.post") as post:
        agent.flush_buffer("current-device")
    post.assert_not_called()
    assert not buffer_file.exists()
