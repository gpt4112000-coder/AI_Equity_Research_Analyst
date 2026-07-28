#!/bin/bash
# Start the AI Equity Research Platform

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate venv if not already active
if [ -z "$VIRTUAL_ENV" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

echo "=== AI Equity Research Platform ==="

# Start FastAPI backend
echo "[1/2] Starting FastAPI backend on port 8001..."
cd "$BACKEND_DIR"
PYTHONPATH="$BACKEND_DIR" uvicorn app:app --host 0.0.0.0 --port 8001 --reload &
BACKEND_PID=$!

# Serve frontend
echo "[2/2] Starting frontend on port 3000..."
cd "$FRONTEND_DIR"
python3 -m http.server 3000 &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8001"
echo "Frontend: http://localhost:3000"
echo "API Docs: http://localhost:8001/docs"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
