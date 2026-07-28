#!/bin/bash
# Setup script for AI Equity Research Platform

echo "Setting up AI Equity Research environment..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Create virtual environment
echo "[1/5] Creating virtual environment..."
python3 -m venv "$SCRIPT_DIR/.venv"
source "$SCRIPT_DIR/.venv/bin/activate"

# Install dependencies
echo "[2/5] Installing Python dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt"

# Initialize database
echo "[3/5] Initializing database..."
cd "$SCRIPT_DIR/backend"
PYTHONPATH="$SCRIPT_DIR/backend:/home/ubuntu/FinEng" python -c "from data.storage.db import init_db; init_db()"

# Fetch company universe
echo "[4/5] Fetching company universe (BSE + NSE)..."
PYTHONPATH="$SCRIPT_DIR/backend:/home/ubuntu/FinEng" python "$SCRIPT_DIR/scripts/fetch_company_universe.py"

# Import announcements
echo "[5/5] Importing announcements from cache..."
PYTHONPATH="$SCRIPT_DIR/backend:/home/ubuntu/FinEng" python "$SCRIPT_DIR/scripts/import_announcements.py"

# Extract insights
echo "[6/6] Extracting insights..."
PYTHONPATH="$SCRIPT_DIR/backend:/home/ubuntu/FinEng" python "$SCRIPT_DIR/scripts/extract_insights_fast.py"

# Build summaries
echo "[7/7] Building company summaries..."
PYTHONPATH="$SCRIPT_DIR/backend:/home/ubuntu/FinEng" python "$SCRIPT_DIR/scripts/build_company_summaries.py"

chmod +x "$SCRIPT_DIR/run.sh"

echo ""
echo "Setup complete!"
echo ""
echo "To start the application:"
echo "  source .venv/bin/activate"
echo "  ./run.sh"
