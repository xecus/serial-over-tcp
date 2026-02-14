# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based serial communication toolkit that provides three main utilities for serial port bridging and emulation:

1. **serial_tcp_server.py** - A ser2net equivalent that bridges physical serial ports to TCP connections
2. **serial_tcp_client.py** - Creates virtual serial devices that connect to TCP servers
3. **virtual_serial_echo.py** - Creates virtual serial devices that echo back received data (includes Japanese comments)

## Dependencies

The project uses only Python standard library modules plus:
- `pyserial` (required for serial_tcp_server.py only)

Install dependencies:
```bash
pip install pyserial
```

## Development Commands

### Testing
```bash
# Run all tests
python3 -m pytest tests/ -v

# Run tests with coverage
python3 -m pytest tests/ --cov=. --cov-report=html

# Run single test file
python3 -m pytest tests/test_serial_tcp_server.py -v

# Run specific test method
python3 -m pytest tests/test_serial_tcp_server.py::TestSerialToNetworkBridge::test_init -v
```

### Running the Applications

#### Serial TCP Server (ser2net equivalent)
```bash
# Bridge physical serial port to TCP
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 9600

# With full options
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 9600 -d 8 -p N -s 1 -v
```

#### Serial TCP Client (Virtual Serial Device)
```bash
# Connect to TCP server and create virtual device
python3 serial_tcp_client.py localhost 9999 -d /tmp/vserial0

# Connect to virtual device using standard tools
screen /tmp/vserial0 9600
minicom -D /tmp/vserial0
```

#### Virtual Serial Echo Device
```bash
# Create echo device (Japanese interface)
python3 virtual_serial_echo.py /tmp/echo_device -b 9600
```

## Architecture

### Threading Model
All applications use a multi-threaded architecture:
- Main thread handles setup and signal handling
- Separate threads for bidirectional data transfer
- Non-blocking I/O with select() for efficient operation

### Virtual Device Creation
- Uses Python's `pty` module to create pseudo-terminal pairs
- Creates symlinks for custom device paths with atomic operations to prevent race conditions
- Handles cleanup on shutdown via signal handlers and atexit
- Path validation prevents directory traversal attacks

### Data Flow Patterns
- **Server**: Physical Serial ↔ Multiple TCP Clients (max 10 concurrent)
- **Client**: TCP Connection ↔ Virtual Serial Device ↔ Applications
- **Echo**: Virtual Serial Device ↔ Echo Handler

### Security Features
- Path traversal attack prevention in device path validation
- Connection limits to prevent resource exhaustion
- Data size validation and truncation for large packets
- Safe symlink creation with temporary files and atomic moves
- Proper file permissions (0o660) on created devices

### Error Handling
- Comprehensive exception handling with 158 try-except blocks
- Specific exception types for different error conditions
- Reconnection logic with exponential backoff
- Graceful degradation when devices are unavailable
- Signal handlers for clean shutdown (SIGINT, SIGTERM)

### Class Structure
- **SerialToNetworkBridge**: Main server class with client connection management
- **VirtualSerialDevice**: Shared virtual device implementation used by both client and echo
- **SerialTCPClient**: Client with reconnection and threading management
- Thread-safe design with proper locking (RLock for client lists)

## Testing

Comprehensive test suite with 88 test cases covering:
- Unit tests for all major classes and methods
- Integration tests for complete workflows
- Error handling and edge case testing
- Mock-based testing for external dependencies
- Security testing (path traversal, permission errors)
- Threading and concurrency testing

Test coverage includes mocking of system calls (pty, socket, serial) for reliable testing without hardware dependencies.

## Common Development Patterns

- All scripts use argparse for command-line interfaces
- Logging is configured with timestamps and levels
- Virtual devices automatically clean up symlinks on shutdown
- Thread-safe design with proper daemon thread management
- Context managers for resource cleanup (VirtualSerialDevice supports `with` statement)
- Defensive programming with extensive input validation