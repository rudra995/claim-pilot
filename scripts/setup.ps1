# One-time setup on Windows: Python environment, trained models, policy search index, frontend packages.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$python = if ($env:PYTHON) { $env:PYTHON } else { "py -3.11" }
if (-not (Test-Path .venv)) { Invoke-Expression "$python -m venv .venv" }
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -e ".[dev]"

# The models are trained from the seeded synthetic data, so every machine gets the same ones.
if (-not (Test-Path models\denial.ubj)) { & $py -m backend.models.train_denial }
if (-not (Test-Path models\cost_bill_p50.ubj)) { & $py -m backend.models.train_cost }
# Policy search (and the assistant's reference wordings): index the sample policy PDFs in ChromaDB.
if (-not (Test-Path data\chroma)) { & $py -m backend.rag.ingest }

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node 20+ and npm are needed for the frontend" }
Push-Location frontend; npm ci --silent; Pop-Location
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "Setup done. Start with: powershell -ExecutionPolicy Bypass -File scripts\start.ps1"
