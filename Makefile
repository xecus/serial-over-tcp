# Makefile for Serial Communication Toolkit
# Provides common development and testing tasks

.PHONY: help install test test-verbose test-coverage clean lint format type-check all dev-setup

# Default target
help:
	@echo "Serial Communication Toolkit - Available targets:"
	@echo ""
	@echo "Setup and Installation:"
	@echo "  install       Install runtime dependencies"
	@echo "  dev-setup     Install all dependencies (runtime + development)"
	@echo ""
	@echo "Testing:"
	@echo "  test          Run all unit tests"
	@echo "  test-verbose  Run tests with verbose output"
	@echo "  test-coverage Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  lint          Run code linting (flake8)"
	@echo "  format        Format code (black)"
	@echo "  type-check    Run type checking (mypy)"
	@echo ""
	@echo "Utilities:"
	@echo "  clean         Clean up temporary files and cache"
	@echo "  all           Run all quality checks and tests"

# Installation targets
install:
	@echo "Installing runtime dependencies..."
	pip install pyserial

dev-setup:
	@echo "Installing all dependencies..."
	pip install -r requirements.txt

# Testing targets
test:
	@echo "Running unit tests..."
	python3 -m pytest tests/ -v

test-verbose:
	@echo "Running unit tests with verbose output..."
	python3 -m pytest tests/ -v -s

test-coverage:
	@echo "Running tests with coverage..."
	python3 -m pytest tests/ --cov=. --cov-report=term-missing --cov-report=html

# Individual test files (useful for development)
test-server:
	@echo "Testing serial TCP server..."
	python3 -m pytest tests/test_serial_tcp_server.py -v

test-client:
	@echo "Testing serial TCP client..."
	python3 -m pytest tests/test_serial_tcp_client.py -v

test-echo:
	@echo "Testing virtual serial echo..."
	python3 -m pytest tests/test_virtual_serial_echo.py -v

# Code quality targets
lint:
	@echo "Running code linting..."
	python3 -m flake8 --max-line-length=100 --ignore=E203,W503 *.py tests/

format:
	@echo "Formatting code..."
	python3 -m black --line-length=100 *.py tests/

type-check:
	@echo "Running type checking..."
	python3 -m mypy --ignore-missing-imports *.py

# Cleanup
clean:
	@echo "Cleaning up temporary files..."
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .mypy_cache/
	rm -rf build/
	rm -rf dist/

# Run all quality checks
all: lint type-check test
	@echo ""
	@echo "All quality checks and tests completed successfully!"

# Development workflow targets
check: lint type-check
	@echo "Code quality checks completed!"

# Quick test for specific functionality
quick-test:
	@echo "Running quick tests (unittest framework)..."
	python3 -m unittest discover tests/ -v

# Integration test setup (requires manual verification)
integration-setup:
	@echo "Setting up for integration tests..."
	@echo "Note: Integration tests require manual verification"
	@echo ""
	@echo "To test the applications manually:"
	@echo "1. Terminal 1: python3 serial_tcp_server.py /dev/null 9999"
	@echo "2. Terminal 2: python3 serial_tcp_client.py localhost 9999 -d /tmp/vserial0"
	@echo "3. Terminal 3: python3 virtual_serial_echo.py /tmp/echo_device"
	@echo ""
	@echo "Then test with: echo 'test' > /tmp/vserial0"

# Validate environment
validate-env:
	@echo "Validating environment..."
	@python3 --version
	@echo "Python modules:"
	@python3 -c "import sys; import serial; import pty; import socket; print('✓ All required modules available')" 2>/dev/null || echo "✗ Missing required modules"

# Documentation generation (if needed)
docs:
	@echo "Generating documentation..."
	@echo "Documentation is available in README.md and CLAUDE.md"

# Performance testing (basic)
perf-test:
	@echo "Running basic performance tests..."
	@echo "Note: This is a placeholder for performance testing"
	@echo "Consider implementing specific performance benchmarks as needed"