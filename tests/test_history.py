"""Tests for the SQLite history module."""
import pytest
import os
import sqlite3
import tempfile
from unittest.mock import patch
from datetime import datetime

from src.models import DiagnosticResult
from src import history as history_module


@pytest.fixture
def temp_db(tmp_path):
    """Override DB_PATH to use a temporary database for testing."""
    db_path = str(tmp_path / "test_netsentinel.db")
    with patch.object(history_module, "DB_PATH", db_path):
        history_module.init_db()
        yield db_path


def _make_result(score=85, is_demo=False, gateway_reachable=True, internet_reachable=True,
                 dns_success=True, tcp_success=True, latency=15.0, packet_loss=0.0):
    """Helper to create a DiagnosticResult for testing."""
    return DiagnosticResult(
        timestamp=datetime.now().isoformat(),
        health_score=score,
        status="HEALTHY" if score >= 80 else "WARNING",
        system={"hostname": "test-pc", "os": "Windows"},
        interfaces=[{"name": "eth0", "state": "UP", "ipv4": "192.168.1.1"}],
        gateway={"address": "192.168.1.1", "reachable": gateway_reachable, "latency_ms": 2.0, "packet_loss": 0.0},
        internet={"target": "8.8.8.8", "reachable": internet_reachable, "latency_ms": latency, "packet_loss": packet_loss},
        dns=[{"domain": "google.com", "success": dns_success, "response_ms": 20.0}],
        tcp=[{"host": "google.com", "port": 443, "success": tcp_success, "response_ms": 30.0}],
        routes={"default_route": "default via 192.168.1.1"},
        stability={"min_rtt": 10, "avg_rtt": 15, "max_rtt": 20, "stability": "GOOD"},
        diagnostics=[],
        duration_ms=500,
        is_demo=is_demo
    )


class TestInitDb:
    def test_creates_history_table(self, temp_db):
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history'")
        table = cursor.fetchone()
        conn.close()
        assert table is not None

    def test_idempotent_init(self, temp_db):
        """Calling init_db twice should not raise."""
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.init_db()


class TestSaveAndRetrieve:
    def test_save_and_get_history(self, temp_db):
        result = _make_result()
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.save_diagnostic_run(result)
            rows = history_module.get_history(limit=10)
        assert len(rows) == 1
        assert rows[0]["score"] == 85
        assert rows[0]["gateway_status"] == "PASS"
        assert rows[0]["internet_status"] == "PASS"

    def test_save_multiple_runs(self, temp_db):
        with patch.object(history_module, "DB_PATH", temp_db):
            for i in range(5):
                history_module.save_diagnostic_run(_make_result(score=80 + i))
            rows = history_module.get_history(limit=100)
        assert len(rows) == 5

    def test_history_order_is_desc(self, temp_db):
        """History should return newest first."""
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.save_diagnostic_run(_make_result(score=70))
            history_module.save_diagnostic_run(_make_result(score=90))
            rows = history_module.get_history(limit=10)
        assert rows[0]["score"] == 90
        assert rows[1]["score"] == 70

    def test_limit_parameter(self, temp_db):
        with patch.object(history_module, "DB_PATH", temp_db):
            for _ in range(10):
                history_module.save_diagnostic_run(_make_result())
            rows = history_module.get_history(limit=3)
        assert len(rows) == 3


class TestStatusDerivation:
    def test_gateway_fail_status(self, temp_db):
        result = _make_result(gateway_reachable=False)
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.save_diagnostic_run(result)
            rows = history_module.get_history()
        assert rows[0]["gateway_status"] == "FAIL"

    def test_dns_fail_status(self, temp_db):
        result = _make_result(dns_success=False)
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.save_diagnostic_run(result)
            rows = history_module.get_history()
        assert rows[0]["dns_status"] == "FAIL"

    def test_tcp_fail_status(self, temp_db):
        result = _make_result(tcp_success=False)
        with patch.object(history_module, "DB_PATH", temp_db):
            history_module.save_diagnostic_run(result)
            rows = history_module.get_history()
        assert rows[0]["tcp_status"] == "FAIL"


class TestRecordLimit:
    def test_enforces_1000_record_cap(self, temp_db):
        """When 1000+ records exist, oldest should be pruned."""
        with patch.object(history_module, "DB_PATH", temp_db):
            # Insert 1001 records directly for speed
            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            for i in range(1001):
                cursor.execute(
                    "INSERT INTO history (timestamp, score, status, gateway_status, internet_status, dns_status, tcp_status, latency, packet_loss, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), 80, "HEALTHY", "PASS", "PASS", "PASS", "PASS", 10.0, 0.0, False)
                )
            conn.commit()
            conn.close()

            # Now save one more which should trigger pruning
            history_module.save_diagnostic_run(_make_result())

            conn = sqlite3.connect(temp_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
            conn.close()
        assert count <= 1000
