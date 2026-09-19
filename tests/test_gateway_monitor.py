"""Default-gateway detection must pick the lowest-metric route, not the
first 0.0.0.0 line printed."""
import unittest
from unittest.mock import patch

from src.gateway_monitor import (
    _parse_windows_default_gateway, _parse_linux_default_gateway, get_default_gateway,
)

# A real-world shape: VPN adapter listed first with a worse metric, then
# Wi-Fi (the one Windows actually uses), then a Hyper-V virtual switch.
WINDOWS_MULTI = """
===========================================================================
Interface List
 12...00 ff 12 34 56 78 ......TAP-Windows Adapter V9
===========================================================================

IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0       10.8.0.1         10.8.0.2    281
          0.0.0.0          0.0.0.0  192.168.168.251  192.168.168.126     35
          0.0.0.0          0.0.0.0     172.20.0.1       172.20.0.5   5000
===========================================================================
Persistent Routes:
  Network Address          Netmask  Gateway Address  Metric
          0.0.0.0          0.0.0.0       10.0.0.99        1
===========================================================================

IPv6 Route Table
===========================================================================
Active Routes:
 If Metric Network Destination      Gateway
 12    281 ::/0                     fe80::1
===========================================================================
Persistent Routes:
  None
"""

WINDOWS_SINGLE = """
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0  192.168.168.251  192.168.168.126     35
===========================================================================
Persistent Routes:
  None
"""

WINDOWS_ONLINK_AND_GARBAGE = """
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0          On-link      10.0.0.5     10
          0.0.0.0          0.0.0.0       10.0.0.1       10.0.0.5     notanumber
          0.0.0.0          0.0.0.0       10.0.0.2       10.0.0.5     50
===========================================================================
Persistent Routes:
"""


class TestWindowsParsing(unittest.TestCase):
    def test_lowest_metric_wins_not_first_line(self):
        gw, metric = _parse_windows_default_gateway(WINDOWS_MULTI)
        self.assertEqual(gw, "192.168.168.251")
        self.assertEqual(metric, 35)

    def test_persistent_and_ipv6_sections_are_ignored(self):
        # 10.0.0.99 has metric 1 but lives under Persistent Routes
        gw, _ = _parse_windows_default_gateway(WINDOWS_MULTI)
        self.assertNotEqual(gw, "10.0.0.99")

    def test_single_route(self):
        self.assertEqual(_parse_windows_default_gateway(WINDOWS_SINGLE), ("192.168.168.251", 35))

    def test_skips_on_link_and_unparseable_metric(self):
        self.assertEqual(_parse_windows_default_gateway(WINDOWS_ONLINK_AND_GARBAGE), ("10.0.0.2", 50))

    def test_no_default_route(self):
        self.assertEqual(_parse_windows_default_gateway("Active Routes:\n  None\n"), (None, None))

    def test_tie_keeps_first(self):
        out = """Active Routes:
          0.0.0.0          0.0.0.0       10.0.0.1       10.0.0.5     25
          0.0.0.0          0.0.0.0       10.0.0.2       10.0.0.6     25
Persistent Routes:"""
        self.assertEqual(_parse_windows_default_gateway(out), ("10.0.0.1", 25))


class TestLinuxParsing(unittest.TestCase):
    def test_lowest_metric_wins(self):
        out = ("default via 10.8.0.1 dev tun0 proto static metric 50\n"
               "default via 192.168.1.1 dev wlan0 proto dhcp metric 600\n"
               "default via 10.0.0.1 dev eth0 proto dhcp metric 100\n"
               "192.168.1.0/24 dev wlan0 proto kernel scope link src 192.168.1.20\n")
        self.assertEqual(_parse_linux_default_gateway(out), ("10.8.0.1", 50))

    def test_missing_metric_counts_as_zero(self):
        out = "default via 10.0.0.1 dev eth0\ndefault via 10.8.0.1 dev tun0 metric 50\n"
        self.assertEqual(_parse_linux_default_gateway(out), ("10.0.0.1", 0))

    def test_no_default(self):
        self.assertEqual(_parse_linux_default_gateway("10.0.0.0/8 dev eth0\n"), (None, None))


class TestGetDefaultGateway(unittest.TestCase):
    @patch("src.gateway_monitor.platform.system", return_value="Windows")
    @patch("src.gateway_monitor.subprocess.check_output", return_value=WINDOWS_MULTI)
    def test_windows_end_to_end(self, _co, _sys):
        self.assertEqual(get_default_gateway(), {"gateway": "192.168.168.251"})

    @patch("src.gateway_monitor.platform.system", return_value="Windows")
    @patch("src.gateway_monitor.subprocess.check_output", side_effect=OSError("route not found"))
    def test_command_failure_is_not_detected(self, _co, _sys):
        self.assertEqual(get_default_gateway(), {"gateway": "NOT DETECTED"})


if __name__ == "__main__":
    unittest.main()
