#!/usr/bin/env python3
"""
ser2net equivalent Python program
Provides telnet access to serial ports
"""

import socket
import serial
import threading
import time
import argparse
import sys
import signal
import select
import logging

class SerialToNetworkBridge:
    def __init__(self, serial_port, serial_baudrate, network_port, 
                 databits=8, parity='N', stopbits=1, timeout=1):
        self.serial_port = serial_port
        self.serial_baudrate = serial_baudrate
        self.network_port = network_port
        self.databits = databits
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        
        self.running = False
        self.clients = []
        self.serial_conn = None
        self.server_socket = None
        
        # Setup logging
        logging.basicConfig(level=logging.INFO, 
                          format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
    def setup_serial(self):
        """Setup serial connection"""
        try:
            # Convert parity character to pyserial constant
            parity_map = {'N': serial.PARITY_NONE, 'E': serial.PARITY_EVEN, 
                         'O': serial.PARITY_ODD, 'M': serial.PARITY_MARK, 
                         'S': serial.PARITY_SPACE}
            
            # Convert stopbits to pyserial constant
            stopbits_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 
                           2: serial.STOPBITS_TWO}
            
            self.serial_conn = serial.Serial(
                port=self.serial_port,
                baudrate=self.serial_baudrate,
                bytesize=self.databits,
                parity=parity_map.get(self.parity, serial.PARITY_NONE),
                stopbits=stopbits_map.get(self.stopbits, serial.STOPBITS_ONE),
                timeout=self.timeout
            )
            
            self.logger.info(f"Serial port {self.serial_port} opened successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to open serial port {self.serial_port}: {e}")
            return False
    
    def setup_network(self):
        """Setup network server socket"""
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('', self.network_port))
            self.server_socket.listen(5)
            
            self.logger.info(f"Network server listening on port {self.network_port}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to setup network server: {e}")
            return False
    
    def handle_client(self, client_socket, client_address):
        """Handle individual client connection"""
        self.logger.info(f"Client connected from {client_address}")
        
        try:
            # Add client to list
            self.clients.append(client_socket)
            
            # Send welcome message (optional)
            welcome_msg = f"Connected to {self.serial_port} at {self.serial_baudrate} baud\r\n"
            client_socket.send(welcome_msg.encode())
            
            # Handle client data
            while self.running:
                try:
                    # Check for data from client with timeout
                    ready = select.select([client_socket], [], [], 0.1)
                    if ready[0]:
                        data = client_socket.recv(1024)
                        if not data:
                            break
                        
                        # Send data to serial port
                        if self.serial_conn and self.serial_conn.is_open:
                            self.serial_conn.write(data)
                            self.serial_conn.flush()
                            self.logger.debug(f"Sent to serial: {data}")
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    self.logger.error(f"Error handling client data: {e}")
                    break
                    
        except Exception as e:
            self.logger.error(f"Error with client {client_address}: {e}")
        finally:
            # Remove client from list
            if client_socket in self.clients:
                self.clients.remove(client_socket)
            client_socket.close()
            self.logger.info(f"Client {client_address} disconnected")
    
    def serial_to_network_thread(self):
        """Thread function to read from serial and send to all clients"""
        while self.running:
            try:
                if self.serial_conn and self.serial_conn.is_open and self.clients:
                    # Read from serial port
                    if self.serial_conn.in_waiting > 0:
                        data = self.serial_conn.read(self.serial_conn.in_waiting)
                        if data:
                            self.logger.debug(f"Received from serial: {data}")
                            
                            # Send to all connected clients
                            disconnected_clients = []
                            for client in self.clients:
                                try:
                                    client.send(data)
                                except:
                                    disconnected_clients.append(client)
                            
                            # Remove disconnected clients
                            for client in disconnected_clients:
                                if client in self.clients:
                                    self.clients.remove(client)
                                    client.close()
                
                time.sleep(0.01)  # Small delay to prevent busy waiting
                
            except Exception as e:
                self.logger.error(f"Error in serial to network thread: {e}")
                time.sleep(1)
    
    def accept_connections_thread(self):
        """Thread function to accept new connections"""
        while self.running:
            try:
                # Accept connection with timeout
                self.server_socket.settimeout(1.0)
                client_socket, client_address = self.server_socket.accept()
                
                # Start client handler thread
                client_thread = threading.Thread(
                    target=self.handle_client, 
                    args=(client_socket, client_address)
                )
                client_thread.daemon = True
                client_thread.start()
                
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    self.logger.error(f"Error accepting connection: {e}")
                break
    
    def start(self):
        """Start the bridge"""
        self.logger.info("Starting serial to network bridge...")
        
        # Setup serial connection
        if not self.setup_serial():
            return False
        
        # Setup network server
        if not self.setup_network():
            return False
        
        self.running = True
        
        # Start serial to network thread
        serial_thread = threading.Thread(target=self.serial_to_network_thread)
        serial_thread.daemon = True
        serial_thread.start()
        
        # Start connection accept thread
        accept_thread = threading.Thread(target=self.accept_connections_thread)
        accept_thread.daemon = True
        accept_thread.start()
        
        self.logger.info("Bridge started successfully")
        return True
    
    def stop(self):
        """Stop the bridge"""
        self.logger.info("Stopping serial to network bridge...")
        
        self.running = False
        
        # Close all client connections
        for client in self.clients:
            client.close()
        self.clients.clear()
        
        # Close server socket
        if self.server_socket:
            self.server_socket.close()
        
        # Close serial connection
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
        
        self.logger.info("Bridge stopped")

def signal_handler(signum, frame, bridge):
    """Handle interrupt signals"""
    print("\nReceived interrupt signal, stopping...")
    bridge.stop()
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='Serial to Network Bridge (ser2net equivalent)')
    parser.add_argument('serial_port', help='Serial port (e.g., /dev/ttyUSB0, COM1)')
    parser.add_argument('network_port', type=int, help='Network port to listen on')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, 
                       help='Serial baudrate (default: 9600)')
    parser.add_argument('-d', '--databits', type=int, default=8, choices=[5,6,7,8],
                       help='Data bits (default: 8)')
    parser.add_argument('-p', '--parity', default='N', choices=['N','E','O','M','S'],
                       help='Parity (N=None, E=Even, O=Odd, M=Mark, S=Space, default: N)')
    parser.add_argument('-s', '--stopbits', type=float, default=1, choices=[1,1.5,2],
                       help='Stop bits (default: 1)')
    parser.add_argument('-t', '--timeout', type=float, default=1,
                       help='Serial timeout in seconds (default: 1)')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Create bridge
    bridge = SerialToNetworkBridge(
        serial_port=args.serial_port,
        serial_baudrate=args.baudrate,
        network_port=args.network_port,
        databits=args.databits,
        parity=args.parity,
        stopbits=args.stopbits,
        timeout=args.timeout
    )
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, bridge))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, bridge))
    
    # Start bridge
    if bridge.start():
        try:
            # Keep main thread alive
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    
    bridge.stop()

if __name__ == "__main__":
    main()
