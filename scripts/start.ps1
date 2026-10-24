# Start the API (port 8000) and the frontend (port 5173) on Windows. Close the window to stop both.
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .venv\Scripts\python.exe)) { throw "Run scripts\setup.ps1 first" }

# Load .env into this process so the API sees the settings.
if (Test-Path .env) {
    Get-Content .env | Where-Object { $_ -match '^\s*([A-Z_]+)=(.*)$' } | ForEach-Object {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Split('#')[0].Trim())
    }
}

$api = Start-Process -PassThru -NoNewWindow .venv\Scripts\python.exe -ArgumentList "-m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000"
Write-Host "API:      http://127.0.0.1:8000/api/ready"
Write-Host "Frontend: http://127.0.0.1:5173"
try {
    Push-Location frontend
    npm run dev -- --host 127.0.0.1
} finally {
    Pop-Location
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
