#!/usr/bin/env python3
"""
Unit tests for serial_tcp_server.py
"""

import unittest
import unittest.mock as mock
import socket
import threading
import time
import tempfile
import sys
import os
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from serial_tcp_server import SerialToNetworkBridge


class TestSerialToNetworkBridge(unittest.TestCase):
    """Test cases for SerialToNetworkBridge class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.test_port = 9998  # Use different port for testing
        self.bridge = SerialToNetworkBridge(
            serial_port="/dev/null",  # Use null device for testing
            serial_baudrate=9600,
            network_port=self.test_port
        )
        
    def tearDown(self):
        """Clean up after tests"""
        if hasattr(self, 'bridge') and self.bridge:
            self.bridge.stop()
    
    def test_init(self):
        """Test bridge initialization"""
        self.assertEqual(self.bridge.serial_port, "/dev/null")
        self.assertEqual(self.bridge.serial_baudrate, 9600)
        self.assertEqual(self.bridge.network_port, self.test_port)
        self.assertEqual(self.bridge.databits, 8)
        self.assertEqual(self.bridge.parity, 'N')
        self.assertEqual(self.bridge.stopbits, 1)
        self.assertEqual(self.bridge.timeout, 1)
        self.assertFalse(self.bridge.running)
        self.assertEqual(self.bridge.max_clients, 10)
    
    def test_init_with_custom_params(self):
        """Test bridge initialization with custom parameters"""
        bridge = SerialToNetworkBridge(
            serial_port="/dev/ttyUSB0",
            serial_baudrate=115200,
            network_port=8888,
            databits=7,
            parity='E',
            stopbits=2,
            timeout=2
        )
        
        self.assertEqual(bridge.serial_port, "/dev/ttyUSB0")
        self.assertEqual(bridge.serial_baudrate, 115200)
        self.assertEqual(bridge.network_port, 8888)
        self.assertEqual(bridge.databits, 7)
        self.assertEqual(bridge.parity, 'E')
        self.assertEqual(bridge.stopbits, 2)
        self.assertEqual(bridge.timeout, 2)
    
    @mock.patch('serial_tcp_server.serial.Serial')
    def test_setup_serial_success(self, mock_serial):
        """Test successful serial setup"""
        mock_instance = mock.MagicMock()
        mock_serial.return_value = mock_instance
        
        result = self.bridge.setup_serial()
        
        # Verify return value and state
        self.assertTrue(result)
        self.assertIsNotNone(self.bridge.serial_conn)
        self.assertEqual(self.bridge.serial_conn, mock_instance)
        
        # Verify serial connection was created with correct parameters
        mock_serial.assert_called_once_with(
            port=self.bridge.serial_port,
            baudrate=self.bridge.serial_baudrate,
            bytesize=self.bridge.databits,
            parity=self.bridge.parity,
            stopbits=self.bridge.stopbits,
            timeout=self.bridge.timeout
        )
    
    @mock.patch('serial_tcp_server.serial.Serial')
    def test_setup_serial_failure(self, mock_serial):
        """Test serial setup failure"""
        mock_serial.side_effect = Exception("Serial port not found")
        
        result = self.bridge.setup_serial()
        
        self.assertFalse(result)
        self.assertIsNone(self.bridge.serial_conn)
    
    def test_setup_network_success(self):
        """Test successful network setup"""
        result = self.bridge.setup_network()
        
        self.assertTrue(result)
        self.assertIsNotNone(self.bridge.server_socket)
        
        # Cleanup
        if self.bridge.server_socket:
            self.bridge.server_socket.close()
    
    def test_setup_network_failure_port_in_use(self):
        """Test network setup failure when port is in use"""
        # Create a socket to occupy the port
        blocking_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocking_socket.bind(('', self.test_port))
        blocking_socket.listen(1)
        
        try:
            result = self.bridge.setup_network()
            self.assertFalse(result)
        finally:
            blocking_socket.close()
    
    def test_client_connection_limit(self):
        """Test client connection limit enforcement"""
        # Set a lower limit for testing
        self.bridge.max_clients = 2
        
        # Mock clients list
        mock_client1 = mock.MagicMock()
        mock_client2 = mock.MagicMock()
        mock_client3 = mock.MagicMock()
        
        self.bridge.clients = [mock_client1, mock_client2]
        
        # Test that third client is rejected
        with mock.patch.object(self.bridge, 'logger') as mock_logger:
            self.bridge.handle_client(mock_client3, ('127.0.0.1', 12345))
            
            mock_logger.warning.assert_called()
            mock_client3.close.assert_called()
    
    @mock.patch('serial_tcp_server.select.select')
    def test_handle_client_data_flow(self, mock_select):
        """Test client data handling"""
        mock_client = mock.MagicMock()
        mock_client.recv.return_value = b'test data'
        
        # Mock serial connection
        mock_serial = mock.MagicMock()
        mock_serial.is_open = True
        self.bridge.serial_conn = mock_serial
        
        # Mock select to return data available first time, then empty
        call_count = [0]
        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([mock_client], [], [])
            else:
                # Second call - set running=False to exit loop
                self.bridge.running = False
                return ([], [], [])
        
        mock_select.side_effect = select_side_effect
        
        # Start with running=True
        self.bridge.running = True
        
        self.bridge.handle_client(mock_client, ('127.0.0.1', 12345))
        
        # Verify data was written to serial
        mock_serial.write.assert_called_with(b'test data')
        mock_serial.flush.assert_called()
    
    def test_stop_graceful_shutdown(self):
        """Test graceful shutdown"""
        # Set up some mock state
        mock_client = mock.MagicMock()
        self.bridge.clients = [mock_client]
        self.bridge.running = True
        
        # Mock server socket
        mock_server_socket = mock.MagicMock()
        self.bridge.server_socket = mock_server_socket
        
        # Mock serial connection
        mock_serial = mock.MagicMock()
        mock_serial.is_open = True
        self.bridge.serial_conn = mock_serial
        
        self.bridge.stop()
        
        # Verify shutdown sequence
        self.assertFalse(self.bridge.running)
        self.assertTrue(self.bridge.shutdown_event.is_set())
        mock_server_socket.close.assert_called()
        mock_client.close.assert_called()
        mock_serial.close.assert_called()
        self.assertEqual(len(self.bridge.clients), 0)
    
    def test_thread_lifecycle_management(self):
        """Test thread creation and cleanup"""
        # Mock thread objects
        mock_thread1 = mock.MagicMock()
        mock_thread1.is_alive.return_value = True  # Both threads are alive
        mock_thread2 = mock.MagicMock()
        mock_thread2.is_alive.return_value = True  # This thread is still alive
        
        self.bridge.client_threads = [mock_thread1, mock_thread2]
        
        self.bridge.stop()
        
        # Verify both threads had join called (only alive threads get join called)
        mock_thread1.join.assert_called_with(timeout=2.0)
        mock_thread2.join.assert_called_with(timeout=2.0)
    
    def test_concurrent_client_additions(self):
        """Test thread-safe client list operations"""
        import threading
        
        # Simulate concurrent client additions
        def add_client():
            mock_client = mock.MagicMock()
            with self.bridge.clients_lock:
                self.bridge.clients.append(mock_client)
        
        # Create multiple threads adding clients
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=add_client)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify all clients were added safely
        self.assertEqual(len(self.bridge.clients), 5)
    
    def test_error_handling_in_client_thread(self):
        """Test error handling when client thread operations fail"""
        mock_client = mock.MagicMock()
        mock_client.recv.side_effect = socket.error("Connection reset")
        
        # Mock serial connection
        mock_serial = mock.MagicMock()
        mock_serial.is_open = True
        self.bridge.serial_conn = mock_serial
        
        # Mock select to return client data
        with mock.patch('serial_tcp_server.select.select') as mock_select:
            mock_select.return_value = ([mock_client], [], [])
            
            # Set running to False after first iteration to avoid infinite loop
            self.bridge.running = True
            
            # This should handle the socket error gracefully
            # Run in a separate thread with controlled termination
            import threading
            
            def run_thread():
                try:
                    self.bridge.handle_client(mock_client, ('127.0.0.1', 12345))
                except Exception:
                    pass  # Expected due to mocked error
            
            thread = threading.Thread(target=run_thread, daemon=True)
            thread.start()
            
            # Allow some time for the method to run and encounter the error
            time.sleep(0.1)
            self.bridge.running = False
            thread.join(timeout=1.0)
            
            # Client should be removed from list after error
            self.assertNotIn(mock_client, self.bridge.clients)
    
    def test_server_socket_accept_timeout(self):
        """Test server socket accept timeout handling"""
        mock_server_socket = mock.MagicMock()
        mock_server_socket.accept.side_effect = socket.timeout("Accept timeout")
        self.bridge.server_socket = mock_server_socket
        self.bridge.running = True
        
        # This should handle timeout gracefully and continue
        # Run in a separate thread with controlled termination
        import threading
        
        def run_thread():
            try:
                self.bridge.accept_connections_thread()
            except Exception:
                pass  # Expected due to mocked error
        
        thread = threading.Thread(target=run_thread, daemon=True)
        thread.start()
        
        # Allow some time for the method to run and encounter the error
        time.sleep(0.1)
        self.bridge.running = False
        thread.join(timeout=1.0)
        
        # Should have called accept at least once
        mock_server_socket.accept.assert_called()
    
    def test_serial_connection_interrupted(self):
        """Test handling of serial connection interruption"""
        mock_serial = mock.MagicMock()
        mock_serial.is_open = True
        mock_serial.in_waiting = 10
        mock_serial.read.side_effect = [b'data', Exception("Serial disconnected")]
        self.bridge.serial_conn = mock_serial
        
        # Mock clients to send data to
        mock_client = mock.MagicMock()
        self.bridge.clients = [mock_client]
        
        self.bridge.running = True
        self.bridge.shutdown_event.clear()
        
        # Run in a separate thread with controlled termination
        import threading
        
        def run_thread():
            try:
                self.bridge.serial_to_network_thread()
            except Exception:
                pass  # Expected due to mocked error
        
        thread = threading.Thread(target=run_thread, daemon=True)
        thread.start()
        
        # Allow some time for the method to run and encounter the error
        time.sleep(0.1)
        self.bridge.running = False
        thread.join(timeout=1.0)
        
        # Should have attempted to read from serial
        self.assertEqual(mock_serial.read.call_count, 2)
    
    @mock.patch('serial_tcp_server.select.select')
    def test_serial_to_network_thread_data_broadcast(self, mock_select):
        """Test serial to network data broadcasting"""
        # Mock serial connection with data
        mock_serial = mock.MagicMock()
        mock_serial.is_open = True
        mock_serial.in_waiting = 5
        mock_serial.read.return_value = b'hello'
        self.bridge.serial_conn = mock_serial
        
        # Mock clients
        mock_client1 = mock.MagicMock()
        mock_client2 = mock.MagicMock()
        self.bridge.clients = [mock_client1, mock_client2]
        
        # Mock shutdown event for controlled termination
        self.bridge.shutdown_event = threading.Event()
        
        # Mock select to trigger data processing once, then signal shutdown
        call_count = [0]
        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call - simulate data available
                return ([mock_serial], [], [])
            else:
                # Subsequent calls - signal shutdown and return no data
                self.bridge.shutdown_event.set()
                return ([], [], [])
        
        mock_select.side_effect = select_side_effect
        
        # Set running to True and start thread
        self.bridge.running = True
        
        # Run the thread function directly with controlled shutdown
        # Run in a separate thread with controlled termination
        
        def run_thread():
            try:
                self.bridge.serial_to_network_thread()
            except Exception:
                pass  # Expected due to mocked error
        
        thread = threading.Thread(target=run_thread, daemon=True)
        thread.start()
        
        # Allow some time for the method to run
        time.sleep(0.1)
        self.bridge.running = False
        thread.join(timeout=1.0)
        
        # Verify data was sent to both clients
        mock_client1.send.assert_called_with(b'hello')
        mock_client2.send.assert_called_with(b'hello')
    
    def test_signal_handler_setup(self):
        """Test that signal handlers are properly configured"""
        import signal
        
        # This test verifies that the signal handler function exists
        # and can be called without error
        from serial_tcp_server import signal_handler
        
        # Test that the function exists and is callable
        self.assertTrue(callable(signal_handler))
        
        # Test with mock frame (signal handler should handle None bridge)
        import serial_tcp_server
        original_bridge = serial_tcp_server.bridge_instance
        serial_tcp_server.bridge_instance = None
        
        try:
            with self.assertRaises(SystemExit):
                signal_handler(signal.SIGINT, None)
        finally:
            serial_tcp_server.bridge_instance = original_bridge


class TestCommandLineArguments(unittest.TestCase):
    """Test command line argument parsing"""
    
    def test_valid_arguments_parsing(self):
        """Test parsing of valid command line arguments"""
        import serial_tcp_server
        
        # Mock sys.argv
        with mock.patch('sys.argv', ['serial_tcp_server.py', '/dev/ttyUSB0', '9999']):
            parser = serial_tcp_server.argparse.ArgumentParser(description='Serial to TCP Bridge')
            parser.add_argument('serial_port', help='Serial port device')
            parser.add_argument('network_port', type=int, help='TCP port to listen on')
            parser.add_argument('-b', '--baudrate', type=int, default=9600, help='Serial baudrate')
            parser.add_argument('-d', '--databits', type=int, default=8, choices=[5,6,7,8], help='Data bits')
            parser.add_argument('-p', '--parity', default='N', choices=['N','E','O'], help='Parity')
            parser.add_argument('-s', '--stopbits', type=int, default=1, choices=[1,2], help='Stop bits')
            parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
            
            args = parser.parse_args(['/dev/ttyUSB0', '9999'])
            
            self.assertEqual(args.serial_port, '/dev/ttyUSB0')
            self.assertEqual(args.network_port, 9999)
            self.assertEqual(args.baudrate, 9600)  # default
            self.assertEqual(args.databits, 8)     # default
            self.assertEqual(args.parity, 'N')     # default
            self.assertEqual(args.stopbits, 1)     # default
            self.assertFalse(args.verbose)         # default
    
    def test_custom_arguments_parsing(self):
        """Test parsing with custom arguments"""
        import serial_tcp_server
        
        parser = serial_tcp_server.argparse.ArgumentParser(description='Serial to TCP Bridge')
        parser.add_argument('serial_port', help='Serial port device')
        parser.add_argument('network_port', type=int, help='TCP port to listen on')
        parser.add_argument('-b', '--baudrate', type=int, default=9600, help='Serial baudrate')
        parser.add_argument('-d', '--databits', type=int, default=8, choices=[5,6,7,8], help='Data bits')
        parser.add_argument('-p', '--parity', default='N', choices=['N','E','O'], help='Parity')
        parser.add_argument('-s', '--stopbits', type=int, default=1, choices=[1,2], help='Stop bits')
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
        
        args = parser.parse_args(['/dev/ttyUSB1', '8888', '-b', '115200', '-d', '7', '-p', 'E', '-s', '2', '-v'])
        
        self.assertEqual(args.serial_port, '/dev/ttyUSB1')
        self.assertEqual(args.network_port, 8888)
        self.assertEqual(args.baudrate, 115200)
        self.assertEqual(args.databits, 7)
        self.assertEqual(args.parity, 'E')
        self.assertEqual(args.stopbits, 2)
        self.assertTrue(args.verbose)
    
    def test_invalid_port_argument(self):
        """Test invalid port argument handling"""
        import serial_tcp_server
        
        parser = serial_tcp_server.argparse.ArgumentParser(description='Serial to TCP Bridge')
        parser.add_argument('serial_port', help='Serial port device')
        parser.add_argument('network_port', type=int, help='TCP port to listen on')
        
        with self.assertRaises(SystemExit):
            parser.parse_args(['/dev/ttyUSB0', 'invalid_port'])
    
    def test_invalid_choice_arguments(self):
        """Test invalid choice arguments"""
        import serial_tcp_server
        
        parser = serial_tcp_server.argparse.ArgumentParser(description='Serial to TCP Bridge')
        parser.add_argument('serial_port', help='Serial port device')
        parser.add_argument('network_port', type=int, help='TCP port to listen on')
        parser.add_argument('-d', '--databits', type=int, default=8, choices=[5,6,7,8], help='Data bits')
        parser.add_argument('-p', '--parity', default='N', choices=['N','E','O'], help='Parity')
        
        # Invalid databits
        with self.assertRaises(SystemExit):
            parser.parse_args(['/dev/ttyUSB0', '9999', '-d', '9'])
        
        # Invalid parity
        with self.assertRaises(SystemExit):
            parser.parse_args(['/dev/ttyUSB0', '9999', '-p', 'X'])


class TestSerialToNetworkBridgeIntegration(unittest.TestCase):
    """Integration tests for SerialToNetworkBridge"""
    
    def test_network_setup_and_cleanup(self):
        """Test complete network setup and cleanup cycle"""
        bridge = SerialToNetworkBridge(
            serial_port="/dev/null",
            serial_baudrate=9600,
            network_port=0  # Let OS choose port
        )
        
        # Setup network
        self.assertTrue(bridge.setup_network())
        self.assertIsNotNone(bridge.server_socket)
        
        # Get the actual port assigned
        port = bridge.server_socket.getsockname()[1]
        self.assertGreater(port, 0)
        
        # Cleanup
        bridge.stop()
        self.assertIsNone(bridge.server_socket)
    
    def test_bridge_with_invalid_serial_port(self):
        """Test bridge behavior with invalid serial port"""
        bridge = SerialToNetworkBridge(
            serial_port="/dev/nonexistent_port",
            serial_baudrate=9600,
            network_port=0
        )
        
        # Serial setup should fail gracefully
        self.assertFalse(bridge.setup_serial())
        self.assertIsNone(bridge.serial_conn)
    
    def test_client_limit_enforcement(self):
        """Test that client limit is properly enforced"""
        bridge = SerialToNetworkBridge(
            serial_port="/dev/null",
            serial_baudrate=9600,
            network_port=0
        )
        bridge.max_clients = 1  # Set very low limit for testing
        
        # Simulate adding clients beyond limit
        mock_client1 = mock.MagicMock()
        mock_client2 = mock.MagicMock()
        
        bridge.clients = []
        bridge.clients_lock = threading.Lock()
        
        # First client should be accepted
        with bridge.clients_lock:
            bridge.clients.append(mock_client1)
        
        # Second client should be rejected due to limit
        with mock.patch.object(bridge, 'logger') as mock_logger:
            bridge.handle_client(mock_client2, ('127.0.0.1', 12345))
            mock_logger.warning.assert_called()
            mock_client2.close.assert_called()


if __name__ == '__main__':
    unittest.main()