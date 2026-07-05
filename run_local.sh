# Run local script for bash users
#!/bin/bash

echo "Starting Galatiq Invoice Processing System..."

# Check if DB needs initialization
if [ ! -f "inventory.db" ]; then
    echo "Database not found. Initializing database..."
    .venv/bin/python src/main.py init-db
fi

# Start backend in background
echo "Starting Backend Server on http://127.0.0.1:8000..."
.venv/bin/python -m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload &
BACKEND_PID=$!

# Start frontend dev server
echo "Starting Frontend Dev Server..."
cd src/ui
npm install
npm run dev

# Terminate backend on exit
trap "kill $BACKEND_PID" EXIT
