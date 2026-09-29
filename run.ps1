# Moogle launcher: starts Streamlit chat (8501) + static file server (8502)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = "$root\.venv\Scripts\python.exe"
$streamlit = "$root\.venv\Scripts\streamlit.exe"

# Read STATIC_PORT from .env (default 8502)
$staticPort = 8502
if (Test-Path "$root\.env") {
    foreach ($line in Get-Content "$root\.env") {
        if ($line -match '^\s*STATIC_PORT\s*=\s*(\d+)') { $staticPort = $Matches[1] }
    }
}

Write-Host "Starting static file server on port $staticPort ..." -ForegroundColor Cyan
$staticJob = Start-Process -FilePath $py -ArgumentList "-m", "http.server", $staticPort, "--bind", "127.0.0.1", "--directory", "$root\static" -PassThru -WindowStyle Hidden

Write-Host "Starting Streamlit on port 8501 ..." -ForegroundColor Cyan
& $streamlit run streamlit_app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false

# Clean up static server when Streamlit exits
Stop-Process -Id $staticJob.Id -Force -ErrorAction SilentlyContinue
Write-Host "Stopped static server." -ForegroundColor Yellow
