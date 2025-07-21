#!/usr/bin/env python3
"""
Unit tests for serial_tcp_client.py
"""

import unittest
import unittest.mock as mock
import socket
import time
import tempfile
import sys
import os
import errno
from pathlib import Path

# Add parent directory to path to import modules

sys.path.insert(0, str(Path(__file__).parent.parent))

from serial_tcp_client import SerialTCPClient, VirtualSerialDevice


class TestVirtualSerialDevice(unittest.TestCase):
    """Test cases for VirtualSerialDevice class"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_device_path = os.path.join(self.temp_dir, "test_device")

    def tearDown(self):
        """Clean up after tests"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_validate_device_path_valid(self):
        """Test device path validation with valid path"""
        device = VirtualSerialDevice()
        self.assertTrue(device._validate_device_path(self.test_device_path))

    def test_validate_device_path_relative(self):
        """Test device path validation with relative path"""
        device = VirtualSerialDevice()
        self.assertFalse(device._validate_device_path("relative/path"))

    def test_validate_device_path_traversal(self):
        """Test device path validation with path traversal"""
        device = VirtualSerialDevice()
        malicious_path = os.path.join(self.temp_dir, "..", "malicious")
        self.assertFalse(device._validate_device_path(malicious_path))

    def test_validate_device_path_nonexistent_parent(self):
        """Test device path validation with non-existent parent"""
        device = VirtualSerialDevice()
        bad_path = "/nonexistent/directory/device"
        self.assertFalse(device._validate_device_path(bad_path))

    def test_init_with_valid_path(self):
        """Test initialization with valid device path"""
        device = VirtualSerialDevice(self.test_device_path)
        self.assertEqual(device.device_path, self.test_device_path)

    def test_init_with_invalid_path(self):
        """Test initialization with invalid device path"""
        with self.assertRaises(ValueError):
            VirtualSerialDevice("relative/path")

    @mock.patch('serial_tcp_client.pty.openpty')
    @mock.patch('serial_tcp_client.os.ttyname')
    def test_create_virtual_device_success(self, mock_ttyname, mock_openpty):
        """Test successful virtual device creation"""
        mock_openpty.return_value = (3, 4)  # Mock file descriptors
        mock_ttyname.return_value = "/dev/pts/0"

        device = VirtualSerialDevice(self.test_device_path)

        with mock.patch.object(device, '_create_symlink_safely', return_value=True):
            result = device.create_virtual_device()

        self.assertTrue(result)
        self.assertEqual(device.master_fd, 3)
        self.assertEqual(device.slave_fd, 4)
        self.assertEqual(device.slave_name, "/dev/pts/0")

    @mock.patch('serial_tcp_client.pty.openpty')
    def test_create_virtual_device_pty_failure(self, mock_openpty):
        """Test virtual device creation with pty failure"""
        mock_openpty.side_effect = OSError("pty creation failed")

        device = VirtualSerialDevice(self.test_device_path)
        result = device.create_virtual_device()

        self.assertFalse(result)

    @mock.patch('serial_tcp_client.os.unlink')
    @mock.patch('serial_tcp_client.os.close')
    def test_close_cleanup(self, mock_close, mock_unlink):
        """Test device cleanup on close"""
        device = VirtualSerialDevice(self.test_device_path)
        device.master_fd = 3
        device.slave_fd = 4

        # Mock symlink exists
        with mock.patch('serial_tcp_client.os.path.exists', return_value=True), \
             mock.patch('serial_tcp_client.os.path.islink', return_value=True):
            device.close()

        mock_unlink.assert_called_with(self.test_device_path)
        self.assertEqual(mock_close.call_count, 2)  # Both fds closed
        self.assertIsNone(device.master_fd)
        self.assertIsNone(device.slave_fd)

    def test_create_symlink_safely(self):
        """Test safe symlink creation"""
        device = VirtualSerialDevice()
        target = "/dev/pts/0"

        result = device._create_symlink_safely(target, self.test_device_path)

        self.assertTrue(result)
        self.assertTrue(os.path.islink(self.test_device_path))
        self.assertEqual(os.readlink(self.test_device_path), target)


class TestSerialTCPClient(unittest.TestCase):
    """Test cases for SerialTCPClient class"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_device_path = os.path.join(self.temp_dir, "test_device")
        self.client = SerialTCPClient(
            server_host="localhost",
            server_port=9999,
            virtual_device_path=self.test_device_path
        )

    def tearDown(self):
        """Clean up after tests"""
        if hasattr(self, 'client') and self.client:
            self.client.stop()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_mock_tcp_socket(self, recv_data=b'test data'):
        """Helper method to set up mock TCP socket"""
        mock_tcp_socket = mock.MagicMock()
        mock_tcp_socket.recv.return_value = recv_data
        self.client.tcp_socket = mock_tcp_socket
        return mock_tcp_socket

    def _setup_mock_virtual_device(self, master_fd=5):
        """Helper method to set up mock virtual device"""
        mock_virtual_device = mock.MagicMock()
        mock_virtual_device.master_fd = master_fd
        self.client.virtual_device = mock_virtual_device
        return mock_virtual_device

    def _create_controlled_select_mock(self, first_return, trigger_shutdown=True):
        """Helper method to create a controlled select mock that exits after first call"""
        call_count = [0]

        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return first_return
            else:
                if trigger_shutdown:
                    self.client.shutdown_event.set()
                return ([], [], [])
        return select_side_effect

    def test_init(self):
        """Test client initialization"""
        self.assertEqual(self.client.server_host, "localhost")
        self.assertEqual(self.client.server_port, 9999)
        self.assertEqual(self.client.virtual_device_path, self.test_device_path)
        self.assertFalse(self.client.running)
        self.assertEqual(self.client.max_reconnect_attempts, 5)
        self.assertEqual(self.client.reconnect_delay, 1.0)

    @mock.patch('serial_tcp_client.socket.socket')
    def test_connect_to_server_success(self, mock_socket):
        """Test successful server connection"""
        mock_sock = mock.MagicMock()
        mock_socket.return_value = mock_sock

        result = self.client.connect_to_server()

        self.assertTrue(result)
        mock_sock.connect.assert_called_with(("localhost", 9999))
        self.assertEqual(self.client.tcp_socket, mock_sock)
        self.assertEqual(self.client.reconnect_attempts, 0)

    @mock.patch('serial_tcp_client.socket.socket')
    def test_connect_to_server_failure(self, mock_socket):
        """Test server connection failure"""
        mock_sock = mock.MagicMock()
        mock_sock.connect.side_effect = socket.error("Connection refused")
        mock_socket.return_value = mock_sock

        result = self.client.connect_to_server()

        self.assertFalse(result)
        self.assertIsNone(self.client.tcp_socket)
        mock_sock.close.assert_called()

    @mock.patch('serial_tcp_client.VirtualSerialDevice')
    def test_setup_virtual_device_success(self, mock_device_class):
        """Test successful virtual device setup"""
        mock_device = mock.MagicMock()
        mock_device.create_virtual_device.return_value = True
        mock_device_class.return_value = mock_device

        result = self.client.setup_virtual_device()

        self.assertTrue(result)
        self.assertEqual(self.client.virtual_device, mock_device)
        mock_device.create_virtual_device.assert_called_once()

    @mock.patch('serial_tcp_client.VirtualSerialDevice')
    def test_setup_virtual_device_failure(self, mock_device_class):
        """Test virtual device setup failure"""
        mock_device = mock.MagicMock()
        mock_device.create_virtual_device.return_value = False
        mock_device_class.return_value = mock_device

        result = self.client.setup_virtual_device()

        self.assertFalse(result)

    def test_handle_connection_loss_within_limit(self):
        """Test connection loss handling within retry limit"""
        self.client.running = True
        self.client.reconnect_attempts = 2

        with mock.patch.object(self.client, 'connect_to_server', return_value=True) as mock_connect, \
             mock.patch('time.sleep') as mock_sleep:
            self.client._handle_connection_loss()

            # Successful reconnection resets counter to 0
            self.assertEqual(self.client.reconnect_attempts, 0)
            # Exponential backoff: delay = 1.0 * (2 ** (3-1)) = 4.0
            # (attempts was incremented before reconnection)
            expected_delay = self.client.reconnect_delay * (2 ** (3 - 1))
            mock_sleep.assert_called_once_with(expected_delay)
            mock_connect.assert_called_once()

    def test_handle_connection_loss_exceed_limit(self):
        """Test connection loss handling when exceeding retry limit"""
        self.client.running = True
        self.client.reconnect_attempts = 5

        with mock.patch.object(self.client, 'connect_to_server', return_value=False):
            self.client._handle_connection_loss()

            self.assertFalse(self.client.running)

    @mock.patch('serial_tcp_client.select.select')
    @mock.patch('serial_tcp_client.os.write')
    @mock.patch('serial_tcp_client.time.sleep')
    def test_tcp_to_virtual_thread_data_flow(self, mock_sleep, mock_write, mock_select):
        """Test TCP to virtual device data flow"""
        # Setup mocks using helper methods
        mock_tcp_socket = self._setup_mock_tcp_socket(b'test data')
        self._setup_mock_virtual_device(5)

        # Setup controlled select mock
        mock_select.side_effect = self._create_controlled_select_mock(([mock_tcp_socket], [], []))
        mock_write.return_value = 9  # Length of 'test data'

        # Start with running=True and no shutdown event set
        self.client.running = True
        self.client.shutdown_event.clear()

        self.client.tcp_to_virtual_thread()

        mock_tcp_socket.recv.assert_called()
        mock_write.assert_called_with(5, b'test data')

    @mock.patch('serial_tcp_client.select.select')
    @mock.patch('serial_tcp_client.os.read')
    def test_virtual_to_tcp_thread_data_flow(self, mock_read, mock_select):
        """Test virtual device to TCP data flow"""
        # Mock virtual device with data
        mock_virtual_device = mock.MagicMock()
        mock_virtual_device.master_fd = 5
        self.client.virtual_device = mock_virtual_device

        # Mock TCP socket
        mock_tcp_socket = mock.MagicMock()
        self.client.tcp_socket = mock_tcp_socket

        # Mock select to return data available first time, then empty
        call_count = [0]

        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([5], [], [])
            else:
                # Second call - trigger shutdown and return empty
                self.client.shutdown_event.set()
                return ([], [], [])

        mock_select.side_effect = select_side_effect
        mock_read.return_value = b'response data'

        # Start with running=True and no shutdown event set
        self.client.running = True
        self.client.shutdown_event.clear()

        self.client.virtual_to_tcp_thread()

        mock_read.assert_called_with(5, 4096)
        mock_tcp_socket.sendall.assert_called_with(b'response data')

    @mock.patch('serial_tcp_client.select.select')
    def test_tcp_to_virtual_connection_lost(self, mock_select):
        """Test handling of TCP connection loss"""
        mock_tcp_socket = mock.MagicMock()
        mock_tcp_socket.recv.return_value = b''  # Empty data indicates connection closed
        self.client.tcp_socket = mock_tcp_socket

        mock_select.return_value = ([mock_tcp_socket], [], [])

        with mock.patch.object(self.client, '_handle_connection_loss') as mock_handle_loss:
            # Start with running=True and no shutdown event set
            self.client.running = True
            self.client.shutdown_event.clear()
            self.client.tcp_to_virtual_thread()

            mock_handle_loss.assert_called_once()

    def test_stop_graceful_shutdown(self):
        """Test graceful shutdown"""
        # Set up mock state
        mock_tcp_socket = mock.MagicMock()
        self.client.tcp_socket = mock_tcp_socket

        mock_virtual_device = mock.MagicMock()
        self.client.virtual_device = mock_virtual_device

        self.client.running = True

        self.client.stop()

        # Verify shutdown sequence
        self.assertFalse(self.client.running)
        self.assertTrue(self.client.shutdown_event.is_set())
        mock_tcp_socket.shutdown.assert_called_with(socket.SHUT_RDWR)
        mock_tcp_socket.close.assert_called()
        mock_virtual_device.close.assert_called()
        self.assertIsNone(self.client.tcp_socket)
        self.assertIsNone(self.client.virtual_device)

    def test_reconnection_counter_increment(self):
        """Test that reconnection counter increments properly"""
        self.client.max_reconnect_attempts = 3
        self.client.running = True

        with mock.patch('time.sleep'), \
             mock.patch.object(self.client, 'connect_to_server', return_value=False):

            # First failed reconnection attempt
            initial_attempts = self.client.reconnect_attempts
            self.client._handle_connection_loss()
            self.assertEqual(self.client.reconnect_attempts, initial_attempts + 1)

            # Should still be running after first failure
            if self.client.reconnect_attempts <= self.client.max_reconnect_attempts:
                self.assertTrue(self.client.running)

    def test_successful_reconnection_resets_counter(self):
        """Test that successful reconnection resets the counter"""
        self.client.max_reconnect_attempts = 3
        self.client.reconnect_attempts = 2  # Start with some failed attempts
        self.client.running = True

        with mock.patch('time.sleep'), \
             mock.patch.object(self.client, 'connect_to_server', return_value=True):

            self.client._handle_connection_loss()

            # Counter should be reset to 0 after successful connection
            self.assertEqual(self.client.reconnect_attempts, 0)
            self.assertTrue(self.client.running)

    def test_exceed_reconnection_limit(self):
        """Test behavior when exceeding reconnection limit"""
        self.client.reconnect_attempts = 5
        self.client.max_reconnect_attempts = 5
        self.client.running = True

        with mock.patch.object(self.client, 'connect_to_server', return_value=False):
            self.client._handle_connection_loss()

            # Should stop running when limit exceeded
            self.assertFalse(self.client.running)

    def test_virtual_device_creation_retry(self):
        """Test virtual device creation with initial failure then success"""
        with mock.patch('serial_tcp_client.VirtualSerialDevice') as mock_device_class:
            mock_device = mock.MagicMock()
            mock_device_class.return_value = mock_device

            # First attempt fails, second succeeds
            mock_device.create_virtual_device.side_effect = [False, True]

            # First attempt should fail
            self.assertFalse(self.client.setup_virtual_device())

            # Second attempt should succeed
            self.assertTrue(self.client.setup_virtual_device())
            self.assertEqual(self.client.virtual_device, mock_device)

    def test_data_transmission_error_handling(self):
        """Test error handling during data transmission"""
        # Mock TCP socket with connection reset error (triggers _handle_connection_loss)
        mock_tcp_socket = mock.MagicMock()
        connection_error = socket.error("Connection reset by peer")
        connection_error.errno = errno.ECONNRESET
        mock_tcp_socket.sendall.side_effect = connection_error

        self.client.tcp_socket = mock_tcp_socket

        # Mock virtual device
        mock_virtual_device = mock.MagicMock()
        mock_virtual_device.master_fd = 5
        self.client.virtual_device = mock_virtual_device

        with mock.patch('serial_tcp_client.select.select') as mock_select:
            with mock.patch('serial_tcp_client.os.read') as mock_read:
                with mock.patch.object(self.client, '_handle_connection_loss') as mock_handle_loss:
                    mock_select.return_value = ([5], [], [])
                    mock_read.return_value = b'test data'

                    self.client.running = True
                    self.client.shutdown_event.clear()

                    # Run in a separate thread with controlled termination
                    import threading

                    def run_thread():
                        try:
                            self.client.virtual_to_tcp_thread()
                        except Exception:
                            pass  # Expected due to mocked error

                    thread = threading.Thread(target=run_thread, daemon=True)
                    thread.start()

                    # Allow some time for the method to run and encounter the error
                    time.sleep(0.1)
                    self.client.running = False
                    thread.join(timeout=1.0)

                    # Should have attempted to send data and handle connection loss
                    mock_tcp_socket.sendall.assert_called_with(b'test data')
                    mock_read.assert_called_with(5, 4096)
                    mock_handle_loss.assert_called_once()

    def test_socket_shutdown_error_handling(self):
        """Test handling of socket shutdown errors"""
        mock_tcp_socket = mock.MagicMock()
        mock_tcp_socket.shutdown.side_effect = socket.error("Socket not connected")
        mock_tcp_socket.close.side_effect = socket.error("Socket already closed")

        self.client.tcp_socket = mock_tcp_socket
        self.client.running = True

        # Should handle shutdown errors gracefully
        self.client.stop()

        # Should still complete shutdown despite errors
        self.assertFalse(self.client.running)
        self.assertTrue(self.client.shutdown_event.is_set())
        self.assertIsNone(self.client.tcp_socket)


class TestCommandLineArguments(unittest.TestCase):
    """Test command line argument parsing for serial_tcp_client"""

    def test_valid_client_arguments_parsing(self):
        """Test parsing of valid client command line arguments"""
        import serial_tcp_client

        parser = serial_tcp_client.argparse.ArgumentParser(description='Serial TCP Client')
        parser.add_argument('server_host', help='Server hostname or IP')
        parser.add_argument('server_port', type=int, help='Server port')
        parser.add_argument('-d', '--device', default='/tmp/vserial0', help='Virtual device path')
        parser.add_argument('-r', '--reconnect-attempts', type=int, default=5, help='Reconnection attempts')
        parser.add_argument('-t', '--reconnect-delay', type=float, default=1.0, help='Reconnection delay')
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')

        args = parser.parse_args(['localhost', '9999'])

        self.assertEqual(args.server_host, 'localhost')
        self.assertEqual(args.server_port, 9999)
        self.assertEqual(args.device, '/tmp/vserial0')  # default
        self.assertEqual(args.reconnect_attempts, 5)    # default
        self.assertEqual(args.reconnect_delay, 1.0)     # default
        self.assertFalse(args.verbose)                  # default

    def test_custom_client_arguments_parsing(self):
        """Test parsing with custom client arguments"""
        import serial_tcp_client

        parser = serial_tcp_client.argparse.ArgumentParser(description='Serial TCP Client')
        parser.add_argument('server_host', help='Server hostname or IP')
        parser.add_argument('server_port', type=int, help='Server port')
        parser.add_argument('-d', '--device', default='/tmp/vserial0', help='Virtual device path')
        parser.add_argument('-r', '--reconnect-attempts', type=int, default=5, help='Reconnection attempts')
        parser.add_argument('-t', '--reconnect-delay', type=float, default=1.0, help='Reconnection delay')
        parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')

        args = (
            parser.parse_args(['192.168.1.100', '8888', '-d', '/tmp/myclient', '-r', '10', '-t', '2.5', '-v']))

        self.assertEqual(args.server_host, '192.168.1.100')
        self.assertEqual(args.server_port, 8888)
        self.assertEqual(args.device, '/tmp/myclient')
        self.assertEqual(args.reconnect_attempts, 10)
        self.assertEqual(args.reconnect_delay, 2.5)
        self.assertTrue(args.verbose)

    def test_invalid_client_port_argument(self):
        """Test invalid client port argument handling"""
        import serial_tcp_client

        parser = serial_tcp_client.argparse.ArgumentParser(description='Serial TCP Client')
        parser.add_argument('server_host', help='Server hostname or IP')
        parser.add_argument('server_port', type=int, help='Server port')

        with self.assertRaises(SystemExit):
            parser.parse_args(['localhost', 'invalid_port'])

    def test_missing_required_arguments(self):
        """Test missing required arguments"""
        import serial_tcp_client

        parser = serial_tcp_client.argparse.ArgumentParser(description='Serial TCP Client')
        parser.add_argument('server_host', help='Server hostname or IP')
        parser.add_argument('server_port', type=int, help='Server port')

        # Missing both required arguments
        with self.assertRaises(SystemExit):
            parser.parse_args([])

        # Missing port argument
        with self.assertRaises(SystemExit):
            parser.parse_args(['localhost'])


class TestSerialTCPClientIntegration(unittest.TestCase):
    """Integration tests for SerialTCPClient"""

    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_device_path = os.path.join(self.temp_dir, "test_device")

    def tearDown(self):
        """Clean up after tests"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_signal_handler_setup(self):
        """Test that signal handlers are properly configured"""
        import signal

        # Test that the signal handler function exists
        from serial_tcp_client import signal_handler

        self.assertTrue(callable(signal_handler))

        # Test with mock frame (signal handler should handle None client)
        import serial_tcp_client
        original_client = serial_tcp_client.client_instance
        serial_tcp_client.client_instance = None

        try:
            with self.assertRaises(SystemExit):
                signal_handler(signal.SIGINT, None)
        finally:
            serial_tcp_client.client_instance = original_client

    @mock.patch('serial_tcp_client.socket.socket')
    @mock.patch('serial_tcp_client.VirtualSerialDevice')
    def test_full_startup_sequence(self, mock_device_class, mock_socket):
        """Test complete startup sequence"""
        # Mock successful virtual device creation
        mock_device = mock.MagicMock()
        mock_device.create_virtual_device.return_value = True
        mock_device.device_path = self.test_device_path
        mock_device.slave_name = "/dev/pts/0"
        mock_device_class.return_value = mock_device

        # Mock successful TCP connection
        mock_sock = mock.MagicMock()
        mock_socket.return_value = mock_sock

        client = SerialTCPClient("localhost", 9999, self.test_device_path)

        result = client.start()

        self.assertTrue(result)
        self.assertTrue(client.running)
        self.assertEqual(client.virtual_device, mock_device)
        self.assertEqual(client.tcp_socket, mock_sock)

        # Cleanup
        client.stop()

    def test_reconnection_with_persistent_failure(self):
        """Test behavior when reconnection consistently fails"""
        client = SerialTCPClient("localhost", 9999, self.test_device_path)
        client.max_reconnect_attempts = 2
        client.reconnect_delay = 0.1  # Speed up test

        with mock.patch.object(client, 'connect_to_server', return_value=False), \
             mock.patch('time.sleep') as mock_sleep:
            # Simulate connection loss multiple times
            client.running = True
            client._handle_connection_loss()

            # Should have incremented attempts
            self.assertEqual(client.reconnect_attempts, 1)

            # Try again
            client._handle_connection_loss()
            self.assertEqual(client.reconnect_attempts, 2)

            # Final attempt should stop the client
            client._handle_connection_loss()
            self.assertFalse(client.running)

    def test_device_path_validation_edge_cases(self):
        """Test edge cases in device path validation"""
        temp_subdir = os.path.join(self.temp_dir, "subdir")
        os.makedirs(temp_subdir)

        # Test with valid subdirectory path
        valid_path = os.path.join(temp_subdir, "device")
        client = SerialTCPClient("localhost", 9999, valid_path)
        self.assertEqual(client.virtual_device_path, valid_path)

        # Test with path containing special characters
        special_path = os.path.join(self.temp_dir, "device-with_special.chars")
        client2 = SerialTCPClient("localhost", 9999, special_path)
        self.assertEqual(client2.virtual_device_path, special_path)


if __name__ == '__main__':
    unittest.main()
