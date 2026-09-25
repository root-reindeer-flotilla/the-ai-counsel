#!/bin/bash

# LLM Council - Start script

echo "Starting LLM Council..."
echo ""

# Start backend
echo "Starting backend on http://localhost:8001..."
LLM_COUNCIL_BIND_HOST="${LLM_COUNCIL_BIND_HOST:-0.0.0.0}" uv run python -m backend.main &
BACKEND_PID=$!

# Wait a bit for backend to start
sleep 2

# Start frontend
echo "Starting frontend on http://localhost:5173..."
cd frontend
if command -v bun >/dev/null 2>&1; then
  BUN_CMD="$(command -v bun)"
elif [ -x "/home/patrick/.bun/bin/bun" ]; then
  BUN_CMD="/home/patrick/.bun/bin/bun"
else
  echo "Error: Bun not found. Install Bun or add it to PATH."
  echo "Checked PATH and /home/patrick/.bun/bin/bun"
  kill "$BACKEND_PID" 2>/dev/null
  exit 1
fi

"$BUN_CMD" run dev --host &
FRONTEND_PID=$!

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait
