<#
.SYNOPSIS
    Builds the single-file WosUtil.exe.

.EXAMPLE
    .\scripts\build.ps1
#>

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    $PyInstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
}
if (-not $PyInstaller) {
    throw "PyInstaller not found. Run: .venv\Scripts\pip install -e '.[dev]'"
}

Write-Host "Running PyInstaller ($PyInstaller)..."
& $PyInstaller --noconfirm --clean (Join-Path $Root "wosutil.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Write-Host "Done: $(Join-Path $Root 'dist\WosUtil.exe')"
