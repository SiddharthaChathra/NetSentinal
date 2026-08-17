import unittest
from unittest.mock import patch, MagicMock
import socket
from src.port_checker import check_ports

class TestPortChecker(unittest.TestCase):
    
    @patch('socket.socket')
    def test_port_success(self, mock_socket):
        # Setup mock socket
        mock_instance = MagicMock()
        mock_socket.return_value = mock_instance
        mock_instance.connect.return_value = None  # connect succeeds
        
        results = check_ports("example.com", [443])
        
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["success"])
        self.assertEqual(results[0]["port"], 443)
        mock_instance.connect.assert_called_with(("example.com", 443))
        
    @patch('socket.socket')
    def test_port_failure(self, mock_socket):
        # Setup mock socket to throw timeout
        mock_instance = MagicMock()
        mock_socket.return_value = mock_instance
        mock_instance.connect.side_effect = socket.timeout("Timed out")
        
        results = check_ports("example.com", [80])
        
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["success"])
        
if __name__ == '__main__':
    unittest.main()
