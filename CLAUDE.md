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

## Running the Applications

### Serial TCP Server (ser2net equivalent)
```bash
# Bridge physical serial port to TCP
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 9600

# With full options
python3 serial_tcp_server.py /dev/ttyUSB0 9999 -b 9600 -d 8 -p N -s 1 -v
```

### Serial TCP Client (Virtual Serial Device)
```bash
# Connect to TCP server and create virtual device
python3 serial_tcp_client.py localhost 9999 -d /tmp/vserial0

# Connect to virtual device using standard tools
screen /tmp/vserial0 9600
minicom -D /tmp/vserial0
```

### Virtual Serial Echo Device
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
- Creates symlinks for custom device paths
- Handles cleanup on shutdown via signal handlers and atexit

### Data Flow Patterns
- **Server**: Physical Serial ↔ Multiple TCP Clients
- **Client**: TCP Connection ↔ Virtual Serial Device ↔ Applications
- **Echo**: Virtual Serial Device ↔ Echo Handler

### Error Handling
- Comprehensive exception handling with logging
- Graceful degradation when devices are unavailable
- Signal handlers for clean shutdown (SIGINT, SIGTERM)

## Testing

No formal test framework is configured. Testing is done manually:

1. **Server Testing**: Connect physical devices and TCP clients
2. **Client Testing**: Use standard serial tools (screen, minicom) with virtual devices
3. **Echo Testing**: Send data to virtual device and verify echo response

## Common Development Patterns

- All scripts use argparse for command-line interfaces
- Logging is configured with timestamps and levels
- Virtual devices automatically clean up symlinks on shutdown
- Thread-safe design with proper daemon thread management