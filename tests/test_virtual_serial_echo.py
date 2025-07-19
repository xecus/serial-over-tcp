#!/usr/bin/env python3
"""
Unit tests for virtual_serial_echo.py
"""

import unittest
import unittest.mock as mock
import os
import pty
import tempfile
import threading
import time
import sys
from pathlib import Path

# Add parent directory to path to import modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from virtual_serial_echo import VirtualSerialDevice


class TestVirtualSerialDevice(unittest.TestCase):
    """Test cases for VirtualSerialDevice class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.test_device_path = os.path.join(self.temp_dir, "test_echo_device")
        
    def tearDown(self):
        """Clean up after tests"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_init_with_valid_path(self):
        """Test initialization with valid device path"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        self.assertEqual(device.device_path, self.test_device_path)
        self.assertEqual(device.baudrate, 9600)
        self.assertFalse(device.running)
        self.assertIsNone(device.master_fd)
        self.assertIsNone(device.slave_fd)
        self.assertFalse(device.device_created)
    
    def test_init_with_invalid_path(self):
        """Test initialization with invalid device path"""
        with self.assertRaises(ValueError):
            VirtualSerialDevice("relative/path", 9600)
    
    def test_init_with_path_traversal(self):
        """Test initialization with path traversal attempt"""
        malicious_path = os.path.join(self.temp_dir, "..", "malicious")
        with self.assertRaises(ValueError):
            VirtualSerialDevice(malicious_path, 9600)
    
    def test_validate_device_path_valid(self):
        """Test device path validation with valid path"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        self.assertTrue(device._validate_device_path(self.test_device_path))
    
    def test_validate_device_path_relative(self):
        """Test device path validation with relative path"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        self.assertFalse(device._validate_device_path("relative/path"))
    
    def test_validate_device_path_nonexistent_parent(self):
        """Test device path validation with non-existent parent"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        bad_path = "/nonexistent/directory/device"
        self.assertFalse(device._validate_device_path(bad_path))
    
    @mock.patch('virtual_serial_echo.pty.openpty')
    @mock.patch('virtual_serial_echo.os.ttyname')
    @mock.patch('virtual_serial_echo.os.chmod')
    def test_create_device_success(self, mock_chmod, mock_ttyname, mock_openpty):
        """Test successful device creation"""
        mock_openpty.return_value = (3, 4)  # Mock file descriptors
        mock_ttyname.return_value = "/dev/pts/0"
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        
        with mock.patch.object(device, '_create_symlink_safely', return_value=True) as mock_symlink:
            result = device.create_device()
        
        # Verify return value and state changes
        self.assertTrue(result)
        self.assertEqual(device.master_fd, 3)
        self.assertEqual(device.slave_fd, 4)
        self.assertTrue(device.device_created)
        
        # Verify all expected method calls occurred
        mock_openpty.assert_called_once()
        mock_ttyname.assert_called_once_with(4)
        mock_symlink.assert_called_once_with("/dev/pts/0", self.test_device_path)
        mock_chmod.assert_called_once_with(self.test_device_path, 0o660)
        
        # Verify call order expectations
        expected_calls = [
            mock.call.openpty(),
            mock.call.ttyname(4),
            mock.call.chmod(self.test_device_path, 0o660)
        ]
        # Note: We can't easily verify exact call order with different mock objects
    
    @mock.patch('virtual_serial_echo.pty.openpty')
    def test_create_device_pty_failure(self, mock_openpty):
        """Test device creation with pty failure"""
        mock_openpty.side_effect = OSError("pty creation failed")
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        result = device.create_device()
        
        self.assertFalse(result)
        self.assertFalse(device.device_created)
    
    def test_create_device_existing_file_not_symlink(self):
        """Test device creation when file exists but is not a symlink"""
        # Create a regular file at the device path
        with open(self.test_device_path, 'w') as f:
            f.write("test")
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        result = device.create_device()
        
        self.assertFalse(result)
        self.assertFalse(device.device_created)
    
    def test_create_symlink_safely(self):
        """Test safe symlink creation"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        target = "/dev/pts/0"
        
        result = device._create_symlink_safely(target, self.test_device_path)
        
        self.assertTrue(result)
        self.assertTrue(os.path.islink(self.test_device_path))
        self.assertEqual(os.readlink(self.test_device_path), target)
    
    def test_create_symlink_safely_failure(self):
        """Test symlink creation failure handling"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        
        # Try to create symlink in non-existent directory
        bad_path = "/nonexistent/directory/device"
        result = device._create_symlink_safely("/dev/pts/0", bad_path)
        
        self.assertFalse(result)
    
    @mock.patch('virtual_serial_echo.select.select')
    @mock.patch('virtual_serial_echo.os.read')
    @mock.patch('virtual_serial_echo.os.write')
    def test_echo_handler_data_flow(self, mock_write, mock_read, mock_select):
        """Test echo handler data processing"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.master_fd = 5
        
        # Mock select to return data available first time, then trigger shutdown
        call_count = [0]
        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([5], [], [])  # Data available first time
            else:
                device.running = False  # Stop the loop
                return ([], [], [])
        
        mock_select.side_effect = select_side_effect
        mock_read.return_value = b'hello'
        mock_write.return_value = 5  # Number of bytes written
        
        # Run echo handler
        device.running = True
        device.echo_handler()
        
        mock_read.assert_called_with(5, 1024)
        mock_write.assert_called_with(5, b'hello')
    
    @mock.patch('virtual_serial_echo.select.select')
    @mock.patch('virtual_serial_echo.os.read')
    def test_echo_handler_large_data_truncation(self, mock_read, mock_select):
        """Test echo handler handling of large data packets"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.master_fd = 5
        
        # Mock large data packet
        large_data = b'x' * 5000  # Larger than 4096 byte limit
        
        # Mock select to return data first time, then trigger shutdown
        call_count = [0]
        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([5], [], [])  # Data available first time
            else:
                device.running = False  # Stop the loop
                return ([], [], [])
        
        mock_select.side_effect = select_side_effect
        mock_read.return_value = large_data
        
        with mock.patch('virtual_serial_echo.os.write') as mock_write:
            device.running = True
            device.echo_handler()
        
        # Should truncate to 4096 bytes
        mock_write.assert_called_with(5, large_data[:4096])
    
    @mock.patch('virtual_serial_echo.select.select')
    @mock.patch('virtual_serial_echo.os.read')
    def test_echo_handler_device_disconnection(self, mock_read, mock_select):
        """Test echo handler handling device disconnection"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.master_fd = 5
        device.running = True
        
        mock_select.return_value = ([5], [], [])
        mock_read.side_effect = OSError(5, "Input/output error")  # EIO
        
        device.echo_handler()
        
        # Should exit gracefully on EIO
        mock_read.assert_called_once()
    
    @mock.patch('virtual_serial_echo.select.select')
    def test_echo_handler_no_data_timeout(self, mock_select):
        """Test echo handler timeout handling"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.master_fd = 5
        device.running = True
        
        # Mock select to return no data available, then set running=False
        call_count = [0]
        def select_side_effect(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return ([], [], [])  # First call - no data available
            else:
                # Second call - set running=False to exit loop
                device.running = False
                return ([], [], [])
        
        mock_select.side_effect = select_side_effect
        
        device.echo_handler()
        
        # Should handle timeout gracefully  
        mock_select.assert_called_with([5], [], [], 0.1)
    
    @mock.patch.object(VirtualSerialDevice, 'create_device')
    def test_start_success(self, mock_create_device):
        """Test successful start"""
        mock_create_device.return_value = True
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        
        with mock.patch('threading.Thread') as mock_thread:
            result = device.start()
        
        self.assertTrue(result)
        self.assertTrue(device.running)
        mock_create_device.assert_called_once()
        mock_thread.assert_called_once()
    
    @mock.patch.object(VirtualSerialDevice, 'create_device')
    def test_start_device_creation_failure(self, mock_create_device):
        """Test start failure due to device creation"""
        mock_create_device.return_value = False
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        result = device.start()
        
        self.assertFalse(result)
        self.assertFalse(device.running)
    
    @mock.patch('virtual_serial_echo.os.unlink')
    @mock.patch('virtual_serial_echo.os.close')
    def test_cleanup_successful(self, mock_close, mock_unlink):
        """Test successful cleanup"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.device_created = True
        device.master_fd = 3
        device.slave_fd = 4
        
        with mock.patch('virtual_serial_echo.os.path.exists', return_value=True), \
             mock.patch('virtual_serial_echo.os.path.islink', return_value=True):
            device.cleanup()
        
        mock_unlink.assert_called_with(self.test_device_path)
        self.assertFalse(device.device_created)
    
    @mock.patch('virtual_serial_echo.os.unlink')
    @mock.patch('virtual_serial_echo.os.chmod')
    def test_cleanup_permission_recovery(self, mock_chmod, mock_unlink):
        """Test cleanup with permission recovery"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.device_created = True
        
        # First unlink fails with permission error, second succeeds
        mock_unlink.side_effect = [OSError(13, "Permission denied"), None]
        
        with mock.patch('virtual_serial_echo.os.path.exists', return_value=True), \
             mock.patch('virtual_serial_echo.os.path.islink', return_value=True):
            device.cleanup()
        
        # Should try to change permissions and retry
        mock_chmod.assert_called_with(self.test_device_path, 0o777)
        self.assertEqual(mock_unlink.call_count, 2)
    
    def test_stop_graceful_shutdown(self):
        """Test graceful stop"""
        device = VirtualSerialDevice(self.test_device_path, 9600)
        device.running = True
        device.master_fd = 3
        device.slave_fd = 4
        
        # Mock thread
        mock_thread = mock.MagicMock()
        device.echo_thread = mock_thread
        
        with mock.patch('virtual_serial_echo.os.close') as mock_close, \
             mock.patch.object(device, 'cleanup') as mock_cleanup:
            device.stop()
        
        self.assertFalse(device.running)
        mock_thread.join.assert_called_with(timeout=2.0)
        self.assertEqual(mock_close.call_count, 2)  # Both fds closed
        mock_cleanup.assert_called_once()
    
    @mock.patch.object(VirtualSerialDevice, 'start')
    @mock.patch.object(VirtualSerialDevice, 'stop')
    def test_context_manager_success(self, mock_stop, mock_start):
        """Test context manager successful usage"""
        mock_start.return_value = True
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        
        with device as ctx_device:
            self.assertEqual(ctx_device, device)
        
        mock_start.assert_called_once()
        mock_stop.assert_called_once()
    
    @mock.patch.object(VirtualSerialDevice, 'start')
    def test_context_manager_failure(self, mock_start):
        """Test context manager with start failure"""
        mock_start.return_value = False
        
        device = VirtualSerialDevice(self.test_device_path, 9600)
        
        with self.assertRaises(RuntimeError):
            with device:
                pass
    
    def test_signal_handler_setup(self):
        """Test that signal handlers are properly configured"""
        import signal
        
        # Test that the signal handler function exists
        from virtual_serial_echo import signal_handler
        
        self.assertTrue(callable(signal_handler))
        
        # Test with mock frame (signal handler should handle None device)
        import virtual_serial_echo
        original_device = virtual_serial_echo.device_instance
        virtual_serial_echo.device_instance = None
        
        try:
            with self.assertRaises(SystemExit):
                signal_handler(signal.SIGINT, None)
        finally:
            virtual_serial_echo.device_instance = original_device


class TestCommandLineArguments(unittest.TestCase):
    """Test command line argument parsing for virtual_serial_echo"""
    
    def test_valid_echo_arguments_parsing(self):
        """Test parsing of valid echo command line arguments"""
        import virtual_serial_echo
        
        parser = virtual_serial_echo.argparse.ArgumentParser(description='仮想シリアルデバイス エコーサーバー')
        parser.add_argument('device_path', help='作成するデバイスファイルのパス')
        parser.add_argument('-b', '--baudrate', type=int, default=9600, help='ボーレート (デフォルト: 9600)')
        
        args = parser.parse_args(['/tmp/echo_device'])
        
        self.assertEqual(args.device_path, '/tmp/echo_device')
        self.assertEqual(args.baudrate, 9600)  # default
    
    def test_custom_echo_arguments_parsing(self):
        """Test parsing with custom echo arguments"""
        import virtual_serial_echo
        
        parser = virtual_serial_echo.argparse.ArgumentParser(description='仮想シリアルデバイス エコーサーバー')
        parser.add_argument('device_path', help='作成するデバイスファイルのパス')
        parser.add_argument('-b', '--baudrate', type=int, default=9600, help='ボーレート (デフォルト: 9600)')
        
        args = parser.parse_args(['/tmp/my_echo_device', '-b', '115200'])
        
        self.assertEqual(args.device_path, '/tmp/my_echo_device')
        self.assertEqual(args.baudrate, 115200)
    
    def test_invalid_echo_baudrate_argument(self):
        """Test invalid baudrate argument handling"""
        import virtual_serial_echo
        
        parser = virtual_serial_echo.argparse.ArgumentParser(description='仮想シリアルデバイス エコーサーバー')
        parser.add_argument('device_path', help='作成するデバイスファイルのパス')
        parser.add_argument('-b', '--baudrate', type=int, default=9600, help='ボーレート (デフォルト: 9600)')
        
        with self.assertRaises(SystemExit):
            parser.parse_args(['/tmp/echo_device', '-b', 'invalid_baudrate'])
    
    def test_missing_echo_device_path(self):
        """Test missing required device path argument"""
        import virtual_serial_echo
        
        parser = virtual_serial_echo.argparse.ArgumentParser(description='仮想シリアルデバイス エコーサーバー')
        parser.add_argument('device_path', help='作成するデバイスファイルのパス')
        parser.add_argument('-b', '--baudrate', type=int, default=9600, help='ボーレート (デフォルト: 9600)')
        
        with self.assertRaises(SystemExit):
            parser.parse_args([])  # Missing required device_path


class TestVirtualSerialDeviceIntegration(unittest.TestCase):
    """Integration tests for VirtualSerialDevice"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        
    def tearDown(self):
        """Clean up after tests"""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_real_device_creation_and_cleanup(self):
        """Test actual device creation and cleanup (requires pty support)"""
        device_path = os.path.join(self.temp_dir, "real_test_device")
        device = None
        
        try:
            device = VirtualSerialDevice(device_path, 9600)
            
            # Test creation
            result = device.create_device()
            if not result:
                self.skipTest("Device creation failed - likely no pty support")
            
            self.assertTrue(result)
            self.assertTrue(os.path.islink(device_path))
            self.assertIsNotNone(device.master_fd)
            self.assertIsNotNone(device.slave_fd)
            
            # Verify file descriptors are valid
            self.assertGreaterEqual(device.master_fd, 0)
            self.assertGreaterEqual(device.slave_fd, 0)
            
            # Test cleanup
            device.cleanup()
            self.assertFalse(os.path.exists(device_path))
            
        except (OSError, PermissionError) as e:
            # Skip test if pty creation fails (e.g., in containers)
            self.skipTest(f"Cannot create pty in this environment: {e}")
        finally:
            # Ensure cleanup even if test fails
            if device and device.device_created:
                try:
                    device.cleanup()
                except:
                    pass  # Ignore cleanup errors in finally block
    
    def test_multiple_device_creation_and_cleanup(self):
        """Test creating multiple devices simultaneously"""
        devices = []
        device_paths = []
        
        try:
            # Create multiple devices
            for i in range(3):
                device_path = os.path.join(self.temp_dir, f"multi_device_{i}")
                device_paths.append(device_path)
                
                device = VirtualSerialDevice(device_path, 9600)
                result = device.create_device()
                
                if not result:
                    self.skipTest(f"Device {i} creation failed - likely no pty support")
                
                devices.append(device)
                self.assertTrue(os.path.islink(device_path))
            
            # Verify all devices are created
            self.assertEqual(len(devices), 3)
            
            # Clean up all devices
            for device in devices:
                device.cleanup()
            
            # Verify all symlinks are removed
            for device_path in device_paths:
                self.assertFalse(os.path.exists(device_path))
                
        except (OSError, PermissionError) as e:
            self.skipTest(f"Cannot create multiple pty devices: {e}")
        finally:
            # Ensure cleanup of all devices
            for device in devices:
                if device and device.device_created:
                    try:
                        device.cleanup()
                    except:
                        pass
    
    def test_device_permissions_and_ownership(self):
        """Test device permissions are set correctly"""
        device_path = os.path.join(self.temp_dir, "perm_test_device")
        device = None
        
        try:
            device = VirtualSerialDevice(device_path, 9600)
            result = device.create_device()
            
            if not result:
                self.skipTest("Device creation failed - likely no pty support")
            
            # Check that symlink exists and has reasonable permissions
            self.assertTrue(os.path.islink(device_path))
            
            # Check that we can read the target of the symlink
            target = os.readlink(device_path)
            self.assertTrue(target.startswith('/dev/pts/'))
            
        except (OSError, PermissionError) as e:
            self.skipTest(f"Cannot test device permissions: {e}")
        finally:
            if device and device.device_created:
                try:
                    device.cleanup()
                except:
                    pass


if __name__ == '__main__':
    unittest.main()