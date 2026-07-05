# Run local script for Windows PowerShell/pwsh
# Starts both FastAPI Backend and Vite Frontend Dev Server

Write-Host "Starting Galatiq Invoice Processing System..." -ForegroundColor Cyan

# Check if DB needs initialization
if (-not (Test-Path inventory.db)) {
    Write-Host "Database not found. Initializing database..." -ForegroundColor Yellow
    .venv\Scripts\python src/main.py init-db
}

# Start backend in a new window/process
Write-Host "Starting Backend Server on http://127.0.0.1:8000..." -ForegroundColor Green
Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload" -NoNewWindow -PassThru

# Start frontend dev server
Write-Host "Starting Frontend Dev Server..." -ForegroundColor Green
Set-Location src/ui
npm install
npm run dev
