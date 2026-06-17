# Pulsar - Graphical audio routing manager for PulseAudio/PipeWire
# Convenience Makefile for common development tasks.
#
# Default goal: help

.PHONY: help run install test lint format clean check-deps

help:  ## Show this help message
	@echo "Pulsar - Graphical audio routing manager for PulseAudio/PipeWire"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Runtime requirement: pactl (from PulseAudio or PipeWire)"

run:  ## Run the application
	python3 src/main.py

install:  ## Run the project install script
	./install.sh

test:  ## Run the test suite
	python3 -m pytest tests/

lint:  ## Lint source code (uses ruff if available, otherwise py_compile)
	@if command -v ruff >/dev/null 2>&1; then \
		echo "Running ruff..."; \
		ruff check src/; \
	else \
		echo "ruff not installed; falling back to py_compile syntax check"; \
		python3 -m py_compile src/main.py; \
	fi

format:  ## Auto-format source code with ruff
	@if command -v ruff >/dev/null 2>&1; then \
		echo "Running ruff format..."; \
		ruff format src/; \
	else \
		echo "ruff not installed; install it with 'pip install ruff' to enable formatting."; \
		exit 1; \
	fi

clean:  ## Remove build artifacts and caches
	find . -type d -name '__pycache__' -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	find . -type f -name '*.pyo' -delete
	rm -rf .pytest_cache
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name '*.egg-info' -exec rm -rf {} +
	@echo "Cleaned."

check-deps:  ## Check that required runtime dependencies are present
	@echo "Checking for pactl..."
	@if command -v pactl >/dev/null 2>&1; then \
		echo "OK: pactl found at $$(command -v pactl)"; \
		pactl --version | head -n 1; \
	else \
		echo "ERROR: pactl is not installed."; \
		echo "Pulsar requires pactl from PulseAudio or PipeWire."; \
		echo "Install it via your distribution's package manager, e.g.:"; \
		echo "  Debian/Ubuntu: sudo apt install pulseaudio-utils"; \
		echo "  Fedora:        sudo dnf install pulseaudio-utils"; \
		echo "  Arch:          sudo pacman -S pulseaudio"; \
		exit 1; \
	fi
	@echo "Checking for python3 tkinter..."
	@if python3 -c "import tkinter" >/dev/null 2>&1; then \
		echo "OK: tkinter is available"; \
	else \
		echo "ERROR: python3 tkinter module not found."; \
		echo "Install it via your distribution's package manager, e.g.:"; \
		echo "  Debian/Ubuntu: sudo apt install python3-tk"; \
		echo "  Fedora:        sudo dnf install python3-tkinter"; \
		echo "  Arch:          sudo pacman -S tk"; \
		exit 1; \
	fi
