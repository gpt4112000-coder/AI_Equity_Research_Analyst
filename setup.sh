#!/bin/bash
# Setup script for GitHub Codespaces

echo "Setting up AI Equity Research environment..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Create virtual environment (needed for Python 3.12 externally-managed)
echo "[1/5] Creating virtual environment..."
python3 -m venv "$SCRIPT_DIR/.venv"
source "$SCRIPT_DIR/.venv/bin/activate"

# Install dependencies
echo "[2/5] Installing Python dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt"

# Initialize database
echo "[3/5] Initializing database..."
cd "$SCRIPT_DIR/backend"
PYTHONPATH="$SCRIPT_DIR/backend" python -c "from data.storage.db import init_db; init_db()"

# Seed companies
echo "[4/5] Seeding companies..."
PYTHONPATH="$SCRIPT_DIR/backend" python "$SCRIPT_DIR/scripts/seed_companies_standalone.py"

# Fetch fundamentals + technicals
echo "[5/5] Fetching fundamentals and technicals..."
PYTHONPATH="$SCRIPT_DIR/backend" python "$SCRIPT_DIR/scripts/fetch_fundamentals.py"
PYTHONPATH="$SCRIPT_DIR/backend" python "$SCRIPT_DIR/scripts/fetch_technicals.py"

chmod +x "$SCRIPT_DIR/run.sh"

echo ""
echo "Setup complete!"
echo ""
echo "To start the application:"
echo "  source .venv/bin/activate"
echo "  ./run.sh"
