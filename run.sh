#!/bin/bash
# Start the AI Equity Research Platform

source /home/ubuntu/anaconda3/etc/profile.d/conda.sh

# Use the BSE_NSE_Announcement environment which has bse, nse, nsedt
conda activate BSE_NSE_Announcement

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "=== AI Equity Research Platform ==="

# Initialize database
echo "[1/3] Initializing database..."
cd "$BACKEND_DIR"
python -m data.storage.db

# Start FastAPI backend
echo "[2/3] Starting FastAPI backend on port 8001..."
cd "$BACKEND_DIR"
uvicorn app:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

# Serve frontend
echo "[3/3] Starting frontend on port 3000..."
cd "$FRONTEND_DIR"
python -m http.server 3000 &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8001"
echo "Frontend: http://localhost:3000"
echo "API Docs: http://localhost:8001/docs"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
