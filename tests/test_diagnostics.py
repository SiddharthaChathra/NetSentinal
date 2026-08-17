import unittest
from src.latency_monitor import classify_latency, classify_packet_loss
from src.diagnostics import run_diagnostics
from src.health_score import calculate_health_score

class TestDiagnostics(unittest.TestCase):

    def test_latency_classification(self):
        self.assertEqual(classify_latency(10), "EXCELLENT")
        self.assertEqual(classify_latency(70), "GOOD")
        self.assertEqual(classify_latency(150), "HIGH")
        self.assertEqual(classify_latency(300), "VERY HIGH")
        self.assertEqual(classify_latency(-5), "UNKNOWN")

    def test_packet_loss_classification(self):
        self.assertEqual(classify_packet_loss(0), "HEALTHY")
        self.assertEqual(classify_packet_loss(2), "WARNING")
        self.assertEqual(classify_packet_loss(10), "HIGH PACKET LOSS")

    def test_health_score_healthy(self):
        data = {
            "gateway": {"reachable": True},
            "internet": {"reachable": True, "packet_loss": 0, "latency_ms": 20},
            "dns": [{"success": True}],
            "tcp": [{"success": True}]
        }
        health = calculate_health_score(data)
        self.assertEqual(health["score"], 100)
        self.assertEqual(health["status"], "HEALTHY")

    def test_health_score_gateway_down(self):
        data = {
            "gateway": {"reachable": False},
            "internet": {"reachable": False, "packet_loss": 100, "latency_ms": 0},
            "dns": [{"success": False}],
            "tcp": [{"success": False}]
        }
        health = calculate_health_score(data)
        # 100 - 40 - 30 - 15 - 15 - 20 (bounded to 0)
        self.assertEqual(health["score"], 0)
        
    def test_diagnostic_engine_gateway_issue(self):
        data = {
            "interfaces": [{"name": "eth0", "state": "UP", "ipv4": "1.1.1.1"}],
            "gateway": {"reachable": False},
            "internet": {"reachable": False},
        }
        diags = run_diagnostics(data)
        self.assertTrue(any("Gateway connectivity problem" in d["title"] for d in diags))

    def test_diagnostic_engine_dns_issue(self):
        data = {
            "interfaces": [{"name": "eth0", "state": "UP", "ipv4": "1.1.1.1"}],
            "gateway": {"reachable": True},
            "internet": {"reachable": True},
            "dns": [{"domain": "google.com", "success": False}]
        }
        diags = run_diagnostics(data)
        self.assertTrue(any("DNS resolution problem" in d["title"] for d in diags))

if __name__ == '__main__':
    unittest.main()
