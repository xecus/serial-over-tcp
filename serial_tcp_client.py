#!/usr/bin/env python3
"""
Serial over TCP Client with Virtual Serial Device
Creates a virtual serial port that forwards data to/from a TCP connection

Copyright (c) 2025 Hiroyuki Ootaguro (Serial over TCP Toolkit)
Licensed under the MIT License. See LICENSE file for details.
"""

import socket
import threading
import time
import argparse
import sys
import signal
import logging
import os
import pty
import select
import termios
import errno
import fcntl
import atexit
from pathlib import Path
from threading import Lock, Event


class VirtualSerialDevice:

    def _validate_device_path(self, path: str) -> bool:
        """Validate device path for security"""
        try:
            path_obj = Path(path)
            # Check if path is absolute
            if not path_obj.is_absolute():
                return False

            # Prevent path traversal attacks
            if '..' in path_obj.parts:
                return False

            # Check parent directory exists and is writable
            parent = path_obj.parent
            if not parent.exists():
                return False

            if not os.access(parent, os.W_OK):
                return False

            return True
        except Exception:
            return False

    def _create_symlink_safely(self, target: str, link_path: str) -> bool:
        """Create symlink safely to avoid race conditions"""
        try:
            # Create temporary symlink first
            self.temp_link_path = f"{link_path}.tmp.{os.getpid()}"
            os.symlink(target, self.temp_link_path)

            # Atomically move to final location
            os.rename(self.temp_link_path, link_path)
            self.temp_link_path = None
            return True
        except OSError as e:
            self.logger.error(f"Symlink creation error: {e}")
            # Clean up temp file if it exists
            if self.temp_link_path and os.path.exists(self.temp_link_path):
                try:
                    os.unlink(self.temp_link_path)
                except OSError:
                    pass
                self.temp_link_path = None
            return False

    def __init__(self, device_path=None):
        # Initialize logger first
        self.logger = logging.getLogger(__name__)

        # Initialize other attributes
        self.device_path = device_path
        self.master_fd = None
        self.slave_fd = None
        self.slave_name = None
        self.temp_link_path = None

        # Validate device path if provided
        if device_path and not self._validate_device_path(device_path):
            raise ValueError(f"Invalid device path: {device_path}")

    def create_virtual_device(self):
        """Create a virtual serial device using pty"""
        try:
            # Create a pseudo-terminal pair
            self.master_fd, self.slave_fd = pty.openpty()

            # Get the slave device name
            self.slave_name = os.ttyname(self.slave_fd)

            # If a custom device path is specified, create a symlink safely
            if self.device_path:
                try:
                    # Remove existing symlink if it exists
                    if os.path.exists(self.device_path) or os.path.islink(self.device_path):
                        if os.path.islink(self.device_path):
                            os.unlink(self.device_path)
                            self.logger.info(f"Removed existing symlink: {self.device_path}")
                        else:
                            self.logger.error(
                                f"Path exists but is not a symlink: {self.device_path}")
                            return False

                    # Create symlink safely
                    if not self._create_symlink_safely(self.slave_name, self.device_path):
                        self.logger.warning(f"Failed to create symlink {self.device_path}")
                        self.logger.info(f"Using default device name: {self.slave_name}")
                        display_name = self.slave_name
                    else:
                        self.logger.info(
                            f"Created symlink: {self.device_path} -> {self.slave_name}")
                        display_name = self.device_path

                except Exception as e:
                    self.logger.warning(f"Failed to create symlink {self.device_path}: {e}")
                    self.logger.info(f"Using default device name: {self.slave_name}")
                    display_name = self.slave_name
            else:
                display_name = self.slave_name

            # Set raw mode on the slave
            try:
                attrs = termios.tcgetattr(self.slave_fd)
                attrs[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK
                              | termios.ISTRIP | termios.IXON)
                attrs[1] &= ~termios.OPOST
                attrs[2] &= ~(termios.CSIZE | termios.PARENB)
                attrs[2] |= termios.CS8
                attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN
                              | termios.ISIG)
                termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs)
            except termios.error as e:
                self.logger.warning(f"Failed to set terminal attributes: {e}")
                # Continue anyway, device might still work

            self.logger.info(f"Virtual serial device created: {display_name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to create virtual serial device: {e}")
            return False

    def close(self):
        """Close the virtual device safely"""
        # Remove symlink if it was created
        if (self.device_path
                and (os.path.exists(self.device_path) or os.path.islink(self.device_path))):
            try:
                os.unlink(self.device_path)
                self.logger.info(f"Removed symlink: {self.device_path}")
            except OSError as e:
                if e.errno != errno.ENOENT:  # Ignore if file doesn't exist
                    self.logger.warning(
                        f"Failed to remove symlink {self.device_path}: {e}")

        # Clean up temporary symlink if it exists
        if self.temp_link_path and os.path.exists(self.temp_link_path):
            try:
                os.unlink(self.temp_link_path)
            except OSError:
                pass

        # Close file descriptors safely
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError as e:
                if e.errno != errno.EBADF:  # Ignore if already closed
                    self.logger.debug(f"Error closing master_fd: {e}")
            self.master_fd = None

        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except OSError as e:
                if e.errno != errno.EBADF:  # Ignore if already closed
                    self.logger.debug(f"Error closing slave_fd: {e}")
            self.slave_fd = None


class SerialTCPClient:
    def __init__(self, server_host, server_port, virtual_device_path=None):
        self.server_host = server_host
        self.server_port = server_port
        self.virtual_device_path = virtual_device_path

        self.running = False
        self.tcp_socket = None
        self.virtual_device = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 1.0
        self.shutdown_event = Event()
        self.connection_lock = Lock()

        # Thread management
        self.tcp_thread = None
        self.virtual_thread = None
        self.threads_lock = Lock()

        # Reconnection state management
        self.reconnection_lock = Lock()
        self.reconnection_in_progress = False

        # Setup logging
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

    def connect_to_server(self):
        """Connect to the ser2net server with retry logic"""
        with self.connection_lock:
            try:
                if self.tcp_socket:
                    try:
                        self.tcp_socket.close()
                    except Exception:
                        pass

                self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_socket.settimeout(10)
                self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

                # Set TCP keepalive parameters if available
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    self.tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    self.tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                if hasattr(socket, 'TCP_KEEPCNT'):
                    self.tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)

                self.tcp_socket.connect((self.server_host, self.server_port))
                with self.reconnection_lock:
                    self.reconnect_attempts = 0

                self.logger.info(f"Connected to server {self.server_host}:{self.server_port}")
                return True

            except socket.error as e:
                self.logger.error(f"Socket error connecting to server: {e}")
                if self.tcp_socket:
                    try:
                        self.tcp_socket.close()
                    except Exception:
                        pass
                    self.tcp_socket = None
                return False
            except Exception as e:
                self.logger.error(f"Failed to connect to server: {e}")
                if self.tcp_socket:
                    try:
                        self.tcp_socket.close()
                    except Exception:
                        pass
                    self.tcp_socket = None
                return False

    def setup_virtual_device(self):
        """Setup virtual serial device"""
        self.virtual_device = VirtualSerialDevice(self.virtual_device_path)
        return self.virtual_device.create_virtual_device()

    def _handle_connection_loss(self):
        """Handle TCP connection loss with reconnection logic"""
        if not self.running:
            return

        # Prevent multiple concurrent reconnection attempts
        with self.reconnection_lock:
            if self.reconnection_in_progress:
                self.logger.debug("Reconnection already in progress, skipping")
                return

            self.reconnection_in_progress = True

        try:
            self._do_reconnection()
        finally:
            with self.reconnection_lock:
                self.reconnection_in_progress = False

    def _do_reconnection(self):
        """Perform the actual reconnection logic"""
        self.reconnect_attempts += 1
        if self.reconnect_attempts <= self.max_reconnect_attempts:
            msg = (f"Attempting to reconnect "
                   f"({self.reconnect_attempts}/{self.max_reconnect_attempts})...")
            self.logger.info(msg)

            # Exponential backoff with interruption support
            delay = min(self.reconnect_delay * (2 ** (self.reconnect_attempts - 1)), 30)
            if self.shutdown_event.wait(delay):
                # Shutdown was requested during delay
                self.logger.info("Shutdown requested during reconnection delay")
                return

            if self.connect_to_server():
                self.logger.info("Reconnection successful")
                # Reset counter on successful reconnection (already done in connect_to_server)

                # Verify virtual device is still valid before restarting threads
                if not self._verify_virtual_device():
                    self.logger.error("Virtual device is no longer valid, stopping client")
                    self.running = False
                    return

                # Restart data transfer threads after successful reconnection
                self._restart_data_threads()
                return
        else:
            self.logger.error(
                "Maximum reconnection attempts reached, stopping client")
            self.running = False

    def tcp_to_virtual_thread(self):
        """Thread function to read from TCP and write to virtual device"""
        while self.running and not self.shutdown_event.is_set():
            try:
                if not self.tcp_socket:
                    time.sleep(0.1)
                    continue

                # Check for data from TCP connection
                try:
                    ready = select.select([self.tcp_socket], [], [], 0.5)
                except (OSError, ValueError) as e:
                    self.logger.error(f"Socket select error: {e}")
                    self._handle_connection_loss()
                    break
                if ready[0]:
                    try:
                        data = self.tcp_socket.recv(4096)
                        if not data:
                            self.logger.warning("TCP connection closed by server")
                            self._handle_connection_loss()
                            continue

                        # Validate data size
                        if len(data) > 8192:
                            self.logger.warning(f"Large TCP packet received: {len(data)} bytes")

                        # Write to virtual device
                        if self.virtual_device and self.virtual_device.master_fd is not None:
                            try:
                                bytes_written = os.write(self.virtual_device.master_fd, data)
                                if bytes_written != len(data):
                                    msg = (f"Partial write to virtual device: "
                                           f"{bytes_written}/{len(data)} bytes")
                                    self.logger.warning(msg)
                                safe_data = data[:50] if len(data) > 50 else data
                                suffix = '...' if len(data) > 50 else ''
                                self.logger.debug(f"TCP -> Virtual: {safe_data}{suffix}")
                            except OSError as e:
                                if e.errno == errno.EIO:
                                    self.logger.info("Virtual device disconnected")
                                    break
                                elif e.errno == errno.EBADF:
                                    self.logger.info("Virtual device file descriptor invalid")
                                    break
                                else:
                                    raise
                    except socket.error as e:
                        if e.errno == errno.ECONNRESET:
                            self.logger.info("TCP connection reset by peer")
                        elif e.errno == errno.ETIMEDOUT:
                            self.logger.info("TCP connection timed out")
                        else:
                            self.logger.error(f"TCP socket error: {e}")
                        self._handle_connection_loss()
                        continue

            except Exception as e:
                if self.running:
                    self.logger.error(f"Unexpected error in TCP to virtual thread: {e}")
                break

    def virtual_to_tcp_thread(self):
        """Thread function to read from virtual device and write to TCP"""
        while self.running and not self.shutdown_event.is_set():
            try:
                # Check for data from virtual device
                if self.virtual_device and self.virtual_device.master_fd is not None:
                    try:
                        ready = select.select([self.virtual_device.master_fd], [], [], 0.5)
                    except (OSError, ValueError) as e:
                        self.logger.error(f"Virtual device select error: {e}")
                        break
                    if ready[0]:
                        try:
                            data = os.read(self.virtual_device.master_fd, 4096)
                            if data:
                                # Send to TCP connection
                                if self.tcp_socket:
                                    try:
                                        self.tcp_socket.sendall(data)
                                        safe_data = data[:50] if len(data) > 50 else data
                                        suffix = '...' if len(data) > 50 else ''
                                        self.logger.debug(f"Virtual -> TCP: {safe_data}{suffix}")
                                    except socket.error as e:
                                        if (e.errno == errno.EPIPE
                                                or e.errno == errno.ECONNRESET):
                                            self.logger.info("TCP connection lost while sending")
                                            self._handle_connection_loss()
                                            continue
                                        else:
                                            raise
                        except OSError as e:
                            if e.errno == errno.EIO:
                                self.logger.info("Virtual device disconnected")
                                break
                            elif e.errno == errno.EBADF:
                                self.logger.info("Virtual device file descriptor invalid")
                                break
                            else:
                                raise

            except Exception as e:
                if self.running:
                    self.logger.error(f"Unexpected error in virtual to TCP thread: {e}")
                break

    def start(self):
        """Start the client"""
        self.logger.info("Starting Serial over TCP client...")

        # Setup virtual device
        if not self.setup_virtual_device():
            return False

        # Connect to server
        if not self.connect_to_server():
            return False

        self.running = True

        # Start data transfer threads
        with self.threads_lock:
            self.tcp_thread = threading.Thread(target=self.tcp_to_virtual_thread)
            self.tcp_thread.daemon = True
            self.tcp_thread.start()

            self.virtual_thread = threading.Thread(target=self.virtual_to_tcp_thread)
            self.virtual_thread.daemon = True
            self.virtual_thread.start()

        device_info = (self.virtual_device.device_path
                       or self.virtual_device.slave_name)
        self.logger.info(f"Client started successfully. Virtual device: {device_info}")

        device_name = self.virtual_device.device_path or self.virtual_device.slave_name
        print(f"\nVirtual serial device available at: {device_name}")
        print("You can connect to it using:")
        print(f"  screen {device_name} 9600")
        print(f"  minicom -D {device_name}")
        print("  or any other serial communication software")
        if self.virtual_device.device_path:
            print(f"  (actual device: {self.virtual_device.slave_name})")
        print("\nPress Ctrl+C to stop the client\n")

        return True

    def stop(self):
        """Stop the client gracefully"""
        self.logger.info("Stopping Serial over TCP client...")

        # Signal all threads to stop
        self.running = False
        self.shutdown_event.set()

        # Wait for threads to finish gracefully
        with self.threads_lock:
            if self.tcp_thread and self.tcp_thread.is_alive():
                self.logger.debug("Waiting for TCP thread to finish...")
                self.tcp_thread.join(timeout=3.0)
                if self.tcp_thread.is_alive():
                    self.logger.warning("TCP thread did not finish within timeout")

            if self.virtual_thread and self.virtual_thread.is_alive():
                self.logger.debug("Waiting for virtual thread to finish...")
                self.virtual_thread.join(timeout=3.0)
                if self.virtual_thread.is_alive():
                    self.logger.warning("Virtual thread did not finish within timeout")

        # Close TCP connection
        if self.tcp_socket:
            try:
                # Graceful shutdown
                self.tcp_socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self.tcp_socket.close()
            except Exception as e:
                self.logger.debug(f"Error closing TCP socket: {e}")
            self.tcp_socket = None

        # Close virtual device
        if self.virtual_device:
            self.virtual_device.close()
            self.virtual_device = None

        self.logger.info("Client stopped")

    def _restart_data_threads(self):
        """Restart data transfer threads after reconnection"""
        self.logger.info("Restarting data transfer threads...")

        with self.threads_lock:
            # Wait for old threads to finish if they're still alive
            if self.tcp_thread and self.tcp_thread.is_alive():
                self.logger.debug("Waiting for TCP thread to finish...")
                self.tcp_thread.join(timeout=2.0)
                if self.tcp_thread.is_alive():
                    self.logger.warning("TCP thread did not finish gracefully, continuing anyway")

            if self.virtual_thread and self.virtual_thread.is_alive():
                self.logger.debug("Waiting for virtual thread to finish...")
                self.virtual_thread.join(timeout=2.0)
                if self.virtual_thread.is_alive():
                    self.logger.warning("Virtual thread did not finish gracefully, continuing anyway")

            # Start new data transfer threads
            self.tcp_thread = threading.Thread(target=self.tcp_to_virtual_thread)
            self.tcp_thread.daemon = True
            self.tcp_thread.start()

            self.virtual_thread = threading.Thread(target=self.virtual_to_tcp_thread)
            self.virtual_thread.daemon = True
            self.virtual_thread.start()

        self.logger.info("Data transfer threads restarted")

    def _verify_virtual_device(self):
        """Verify virtual device is still valid and accessible"""
        if not self.virtual_device:
            self.logger.error("Virtual device object is None")
            return False

        if self.virtual_device.master_fd is None:
            self.logger.error("Virtual device master_fd is None")
            return False

        # Test if file descriptor is still valid
        try:
            # Use fcntl to check if fd is valid without side effects
            fcntl.fcntl(self.virtual_device.master_fd, fcntl.F_GETFD)
            return True
        except (OSError, ValueError) as e:
            self.logger.error(f"Virtual device file descriptor is invalid: {e}")
            return False


# Global variable for signal handler
client_instance = None


def signal_handler(signum, frame):
    """Handle interrupt signals"""
    print(f"\nReceived signal {signum}, stopping...")
    if client_instance:
        client_instance.stop()
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='Serial over TCP Client')
    parser.add_argument('server_host', help='Server hostname or IP address')
    parser.add_argument('server_port', type=int, help='Server port')
    parser.add_argument('-d', '--device', default=None,
                        help='Virtual device path (creates symlink to actual pty device)')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose logging')

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create client
    client = SerialTCPClient(
        server_host=args.server_host,
        server_port=args.server_port,
        virtual_device_path=args.device
    )

    # Setup signal handlers and cleanup
    global client_instance
    client_instance = client
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(lambda: client.stop() if client else None)

    # Start client
    if client.start():
        try:
            # Keep main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    client.stop()


if __name__ == "__main__":
    main()
