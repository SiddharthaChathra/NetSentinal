import unittest
from unittest.mock import patch
import socket
from src.dns_monitor import check_dns

class TestDNSMonitor(unittest.TestCase):
    
    @patch('socket.getaddrinfo')
    def test_dns_success(self, mock_getaddrinfo):
        # Mock successful resolution
        mock_getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.100', 0))]
        
        results = check_dns(["example.com"])
        
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])
        self.assertEqual(results[0]["resolved_ip"], "192.168.1.100")
        self.assertEqual(results[0]["domain"], "example.com")

    @patch('socket.getaddrinfo')
    def test_dns_failure(self, mock_getaddrinfo):
        # Mock DNS failure
        mock_getaddrinfo.side_effect = socket.gaierror("Name or service not known")
        
        results = check_dns(["invalid.local"])
        
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        self.assertIsNone(results[0]["resolved_ip"])

if __name__ == '__main__':
    unittest.main()
