<#
.SYNOPSIS
    Runs PR Sentinel backend and frontend tests.
.DESCRIPTION
    Executes backend Pytest suite followed by frontend Vitest test suite.
    Exits with non-zero exit code if either suite fails.
#>
[CmdletBinding()]
param(
    [switch]$CollectOnly
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$backendDir = Join-Path $rootDir "backend"
$frontendDir = Join-Path $rootDir "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "       PR Sentinel Test Suite Runner      " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Backend Pytest
Write-Host "`n[1/2] Running Backend Tests (pytest)..." -ForegroundColor Yellow
if (-not (Test-Path $backendPython)) {
    Write-Error "Backend Python virtual environment not found at '$backendPython'. Run setup first."
    exit 1
}

$backendArgs = @("-m", "pytest")
if ($CollectOnly) {
    $backendArgs += "--collect-only"
}

Push-Location $backendDir
try {
    & $backendPython $backendArgs
    $backendExitCode = $LASTEXITCODE
    if ($backendExitCode -ne 0) {
        Write-Error "Backend tests failed with exit code $backendExitCode"
        exit $backendExitCode
    }
    Write-Host "Backend tests passed successfully!" -ForegroundColor Green
} finally {
    Pop-Location
}

# 2. Frontend Vitest
Write-Host "`n[2/2] Running Frontend Tests (vitest)..." -ForegroundColor Yellow
$env:VITEST_SKIP_INSTALL_CHECKS = "1"
Push-Location $frontendDir
try {
    npm run test -- --run
    $frontendExitCode = $LASTEXITCODE
    if ($frontendExitCode -ne 0) {
        Write-Error "Frontend tests failed with exit code $frontendExitCode"
        exit $frontendExitCode
    }
    Write-Host "Frontend tests passed successfully!" -ForegroundColor Green
} finally {
    Pop-Location
}

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host " All test suites completed successfully!  " -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
exit 0
