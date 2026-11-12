<#
.SYNOPSIS
    PR Sentinel Development Server Starter.
.DESCRIPTION
    Starts or provides instructions to start backend (FastAPI/Uvicorn on 127.0.0.1:8000)
    and frontend (Vite on 127.0.0.1:5173) development servers.
    Detects already running server instances on ports 8000 and 5173.
    Does NOT print environment values or secrets.
.PARAMETER LaunchWindows
    Launches backend and frontend in separate PowerShell terminal windows.
#>
[CmdletBinding()]
param(
    [switch]$LaunchWindows
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$backendDir = Join-Path $rootDir "backend"
$frontendDir = Join-Path $rootDir "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "       PR Sentinel Development Servers    " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Sanity Checks
if (-not (Test-Path $backendPython)) {
    Write-Error "Backend Python virtual environment not found at '$backendPython'. Run setup first."
    exit 1
}

if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Warning "Frontend node_modules directory not found at '$frontendDir\node_modules'. Run 'npm install' in frontend."
}

# 2. Port Collision Checks (Avoid duplicate instances)
function Test-PortActive {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        return ($conn -ne $null)
    } catch {
        return $false
    }
}

$port8000Active = Test-PortActive -Port 8000
$port5173Active = Test-PortActive -Port 5173

if ($port8000Active) {
    Write-Warning "Port 8000 is already in use by an active listener. An existing backend may already be running."
}
if ($port5173Active) {
    Write-Warning "Port 5173 is already in use by an active listener. An existing frontend dev server may already be running."
}

# 3. Server Details & Target URLs
Write-Host "`nTarget Server Configuration:" -ForegroundColor DarkGray
Write-Host "  Backend:   http://127.0.0.1:8000 (Health: http://127.0.0.1:8000/api/health, OpenAPI: http://127.0.0.1:8000/openapi.json)" -ForegroundColor Green
Write-Host "  Frontend:  http://127.0.0.1:5173 (Vite Proxy -> http://127.0.0.1:8000/api)" -ForegroundColor Green

if ($LaunchWindows) {
    if (-not $port8000Active) {
        Write-Host "`nLaunching Backend (Uvicorn 127.0.0.1:8000) in a new PowerShell window..." -ForegroundColor Green
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$backendDir'; & '$backendPython' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
    } else {
        Write-Host "`nSkipping Backend launch: port 8000 already occupied." -ForegroundColor Yellow
    }

    if (-not $port5173Active) {
        Write-Host "Launching Frontend (Vite 127.0.0.1:5173) in a new PowerShell window..." -ForegroundColor Green
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$frontendDir'; npm run dev -- --host 127.0.0.1 --port 5173"
    } else {
        Write-Host "Skipping Frontend launch: port 5173 already occupied." -ForegroundColor Yellow
    }

    Write-Host "`nTo stop servers:" -ForegroundColor Yellow
    Write-Host "  Press Ctrl+C in each server's PowerShell window, or close the window." -ForegroundColor DarkGray
} else {
    Write-Host "`nTo start development servers manually in separate terminal windows:`n" -ForegroundColor Yellow
    Write-Host "Terminal 1 (Backend):" -ForegroundColor Cyan
    Write-Host "  cd `"$backendDir`""
    Write-Host "  .\.venv\Scripts\Activate.ps1"
    Write-Host "  uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`n"

    Write-Host "Terminal 2 (Frontend):" -ForegroundColor Cyan
    Write-Host "  cd `"$frontendDir`""
    Write-Host "  npm run dev -- --host 127.0.0.1 --port 5173`n"

    Write-Host "How to Stop:" -ForegroundColor Yellow
    Write-Host "  Press Ctrl+C in each terminal window, or close the terminal.`n" -ForegroundColor DarkGray

    Write-Host "Tip: Run '.\scripts\dev.ps1 -LaunchWindows' to launch both development servers automatically in separate windows.`n" -ForegroundColor DarkGray
}
