#!/usr/bin/env python3
"""
Serial over TCP Client with Virtual Serial Device
Creates a virtual serial port that forwards data to/from a TCP connection
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

class VirtualSerialDevice:
    def __init__(self, device_path=None):
        self.device_path = device_path
        self.master_fd = None
        self.slave_fd = None
        self.slave_name = None
        self.logger = logging.getLogger(__name__)
        
    def create_virtual_device(self):
        """Create a virtual serial device using pty"""
        try:
            # Create a pseudo-terminal pair
            self.master_fd, self.slave_fd = pty.openpty()
            
            # Get the slave device name
            self.slave_name = os.ttyname(self.slave_fd)
            
            # If a custom device path is specified, create a symlink
            if self.device_path:
                try:
                    # Remove existing symlink if it exists
                    if os.path.exists(self.device_path) or os.path.islink(self.device_path):
                        os.unlink(self.device_path)
                    
                    # Create symlink
                    os.symlink(self.slave_name, self.device_path)
                    self.logger.info(f"Created symlink: {self.device_path} -> {self.slave_name}")
                    
                    # Use the custom path as the device name for display
                    display_name = self.device_path
                    
                except Exception as e:
                    self.logger.warning(f"Failed to create symlink {self.device_path}: {e}")
                    self.logger.info(f"Using default device name: {self.slave_name}")
                    display_name = self.slave_name
            else:
                display_name = self.slave_name
            
            # Set raw mode on the slave
            attrs = termios.tcgetattr(self.slave_fd)
            attrs[0] &= ~(termios.BRKINT | termios.ICRNL | termios.INPCK | 
                         termios.ISTRIP | termios.IXON)
            attrs[1] &= ~(termios.OPOST)
            attrs[2] &= ~(termios.CSIZE | termios.PARENB)
            attrs[2] |= termios.CS8
            attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
            termios.tcsetattr(self.slave_fd, termios.TCSANOW, attrs)
            
            self.logger.info(f"Virtual serial device created: {display_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to create virtual serial device: {e}")
            return False
    
    def close(self):
        """Close the virtual device"""
        # Remove symlink if it was created
        if self.device_path and os.path.islink(self.device_path):
            try:
                os.unlink(self.device_path)
                self.logger.info(f"Removed symlink: {self.device_path}")
            except Exception as e:
                self.logger.warning(f"Failed to remove symlink {self.device_path}: {e}")
        
        if self.master_fd:
            os.close(self.master_fd)
        if self.slave_fd:
            os.close(self.slave_fd)

class SerialTCPClient:
    def __init__(self, server_host, server_port, virtual_device_path=None):
        self.server_host = server_host
        self.server_port = server_port
        self.virtual_device_path = virtual_device_path
        
        self.running = False
        self.tcp_socket = None
        self.virtual_device = None
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, 
                          format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
    
    def connect_to_server(self):
        """Connect to the ser2net server"""
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(10)
            self.tcp_socket.connect((self.server_host, self.server_port))
            
            self.logger.info(f"Connected to server {self.server_host}:{self.server_port}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to connect to server: {e}")
            return False
    
    def setup_virtual_device(self):
        """Setup virtual serial device"""
        self.virtual_device = VirtualSerialDevice(self.virtual_device_path)
        return self.virtual_device.create_virtual_device()
    
    def tcp_to_virtual_thread(self):
        """Thread function to read from TCP and write to virtual device"""
        while self.running:
            try:
                # Check for data from TCP connection
                ready = select.select([self.tcp_socket], [], [], 0.1)
                if ready[0]:
                    data = self.tcp_socket.recv(1024)
                    if not data:
                        self.logger.warning("TCP connection closed by server")
                        break
                    
                    # Write to virtual device
                    if self.virtual_device and self.virtual_device.master_fd:
                        os.write(self.virtual_device.master_fd, data)
                        self.logger.debug(f"TCP -> Virtual: {data}")
                
            except Exception as e:
                if self.running:
                    self.logger.error(f"Error in TCP to virtual thread: {e}")
                break
    
    def virtual_to_tcp_thread(self):
        """Thread function to read from virtual device and write to TCP"""
        while self.running:
            try:
                # Check for data from virtual device
                if self.virtual_device and self.virtual_device.master_fd:
                    ready = select.select([self.virtual_device.master_fd], [], [], 0.1)
                    if ready[0]:
                        data = os.read(self.virtual_device.master_fd, 1024)
                        if data:
                            # Send to TCP connection
                            if self.tcp_socket:
                                self.tcp_socket.send(data)
                                self.logger.debug(f"Virtual -> TCP: {data}")
                
            except Exception as e:
                if self.running:
                    self.logger.error(f"Error in virtual to TCP thread: {e}")
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
        tcp_thread = threading.Thread(target=self.tcp_to_virtual_thread)
        tcp_thread.daemon = True
        tcp_thread.start()
        
        virtual_thread = threading.Thread(target=self.virtual_to_tcp_thread)
        virtual_thread.daemon = True
        virtual_thread.start()
        
        self.logger.info(f"Client started successfully. Virtual device: {self.virtual_device.device_path or self.virtual_device.slave_name}")
        
        device_name = self.virtual_device.device_path or self.virtual_device.slave_name
        print(f"\nVirtual serial device available at: {device_name}")
        print(f"You can connect to it using:")
        print(f"  screen {device_name} 9600")
        print(f"  minicom -D {device_name}")
        print(f"  or any other serial communication software")
        if self.virtual_device.device_path:
            print(f"  (actual device: {self.virtual_device.slave_name})")
        print(f"\nPress Ctrl+C to stop the client\n")
        
        return True
    
    def stop(self):
        """Stop the client"""
        self.logger.info("Stopping Serial over TCP client...")
        
        self.running = False
        
        # Close TCP connection
        if self.tcp_socket:
            self.tcp_socket.close()
        
        # Close virtual device
        if self.virtual_device:
            self.virtual_device.close()
        
        self.logger.info("Client stopped")

def signal_handler(signum, frame, client):
    """Handle interrupt signals"""
    print("\nReceived interrupt signal, stopping...")
    client.stop()
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
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, client))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, client))
    
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
