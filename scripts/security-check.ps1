# PR Sentinel — Comprehensive Security Validation Pipeline
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " PR Sentinel — Automated Security Check Pipeline" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$allPassed = $true
$checks = @()

function Run-Step {
    param (
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "[RUNNING] $Name..." -ForegroundColor Yellow
    try {
        & $Action
        Write-Host "[PASS] $Name" -ForegroundColor Green
        $script:checks += [PSCustomObject]@{ Check = $Name; Status = "PASS" }
    }
    catch {
        Write-Host "[FAIL] $Name" -ForegroundColor Red
        $script:checks += [PSCustomObject]@{ Check = $Name; Status = "FAIL" }
        $script:allPassed = $false
    }
}

$pythonExe = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"
$ruffExe = Join-Path $projectRoot "backend\.venv\Scripts\ruff.exe"
$banditExe = Join-Path $projectRoot "backend\.venv\Scripts\bandit.exe"
$pytestExe = Join-Path $projectRoot "backend\.venv\Scripts\pytest.exe"

# 1. Verify backend/.env is ignored by Git
Run-Step "Gitignore Verification for backend/.env" {
    $ignored = git check-ignore backend/.env
    if (-not $ignored) {
        throw "backend/.env is NOT ignored by git!"
    }
}

# 2. Static Code Linter (Ruff)
Run-Step "Ruff Backend Linter Check" {
    & $ruffExe check backend
    if ($LASTEXITCODE -ne 0) { throw "Ruff check reported issues" }
}

# 3. Ruff Code Formatting Check
Run-Step "Ruff Backend Format Verification" {
    & $ruffExe format --check backend
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed" }
}

# 4. Bandit AST Security Scanner
Run-Step "Bandit Static Security Audit" {
    & $banditExe -r backend/app -q
    if ($LASTEXITCODE -ne 0) { throw "Bandit reported security vulnerabilities" }
}

# 5. Backend Automated Security Test Suite
Run-Step "Backend Security Test Suite" {
    & $pytestExe -q backend/tests/security
    if ($LASTEXITCODE -ne 0) { throw "Backend security test suite failed" }
}

# 6. Frontend Linter Check
Run-Step "Frontend Linter Check (Oxlint / ESLint)" {
    npm --prefix frontend run lint
    if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed" }
}

# 7. Frontend Security and Component Tests
Run-Step "Frontend Security and Component Tests (Vitest)" {
    npm --prefix frontend test -- --run
    if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
}

# 8. Secret Token Scanner across Repository
Run-Step "Source Code Secret Scanning" {
    $scriptPath = Join-Path $projectRoot "scratch\secret_scanner.py"
    if (Test-Path $scriptPath) {
        & $pythonExe $scriptPath
        if ($LASTEXITCODE -ne 0) { throw "Secret scanner detected hardcoded credentials" }
    }
}

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Security Validation Summary" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

foreach ($c in $checks) {
    $color = "Green"
    if ($c.Status -ne "PASS") {
        $color = "Red"
    }
    Write-Host ($c.Check + " : " + $c.Status) -ForegroundColor $color
}

if ($allPassed) {
    Write-Host ""
    Write-Host "ALL SECURITY CHECKS PASSED. PR Sentinel is secure." -ForegroundColor Green
    exit 0
} else {
    Write-Host ""
    Write-Host "ONE OR MORE SECURITY CHECKS FAILED." -ForegroundColor Red
    exit 1
}
