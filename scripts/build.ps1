<#
.SYNOPSIS
    Builds the single-file WosUtil.exe with Tesseract OCR bundled.

.DESCRIPTION
    1. Downloads the UB Mannheim Tesseract installer into build/ (cached).
    2. Extracts it with 7-Zip and stages only what is needed into
       build/tesseract: tesseract.exe, its runtime DLLs and the English
       language data (the "fast" model by default, otherwise "best").
    3. Smoke-tests the staged tesseract (validates DLLs and language data);
       if the pruned DLL set fails, all DLLs are used instead.
    4. Runs PyInstaller with wosutil.spec producing dist/WosUtil.exe.

    End users only need the resulting executable; no system-wide Tesseract
    installation is required. Nothing besides build/ is written outside the
    repo, so the build artifacts stay gitignored.

.PARAMETER TesseractUrl
    URL of the UB Mannheim 64-bit installer (defaults to the latest 5.5.x).

.PARAMETER TessdataModel
    English language model: "fast" (smaller, default) or "best" (more accurate).

.EXAMPLE
    .\scripts\build.ps1
#>

param(
    [string]$TesseractUrl = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe",
    [ValidateSet("fast", "best")]
    [string]$TessdataModel = "fast"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $Root "build"
$InstallerPath = Join-Path $BuildDir "tesseract-installer.exe"
$ExtractDir = Join-Path $BuildDir "tesseract_src"
$TesseractDir = Join-Path $BuildDir "tesseract"
$TessdataDir = Join-Path $TesseractDir "tessdata"

# Minimal runtime DLL set for tesseract.exe + libtesseract-5/libleptonica-6
# (verified by removing unneeded DLLs one by one while OCR kept working).
# Training/UI/PDF dependencies (pango, cairo, glib, ICU, ...) are excluded.
$RuntimeDlls = @(
    "libarchive-13.dll", "libb2-1.dll", "libbrotlicommon.dll", "libbrotlidec.dll",
    "libbz2-1.dll", "libcurl-4.dll", "libdeflate.dll", "libexpat-1.dll",
    "libgcc_s_seh-1.dll", "libgif-7.dll", "libiconv-2.dll", "libidn2-0.dll",
    "libintl-8.dll", "libjbig-0.dll", "libjpeg-8.dll", "libleptonica-6.dll",
    "libLerc.dll", "liblz4.dll", "liblzma-5.dll", "libopenjp2-7.dll",
    "libpng16-16.dll", "libpsl-5.dll", "libsharpyuv-0.dll", "libssh2-1.dll",
    "libstdc++-6.dll", "libtesseract-5.dll", "libtiff-6.dll",
    "libunistring-5.dll", "libwebp-7.dll", "libwebpmux-3.dll",
    "libwinpthread-1.dll", "libzstd.dll", "zlib1.dll"
)

# --- Locate 7-Zip (needed to extract the Inno Setup installer) ---
$SevenZip = Get-Command 7z -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
if (-not $SevenZip) {
    foreach ($candidate in @(
        "$env:ProgramFiles\7-Zip\7z.exe",
        "${env:ProgramFiles(x86)}\7-Zip\7z.exe",
        "$env:LOCALAPPDATA\Programs\7-Zip\7z.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { $SevenZip = $candidate; break }
    }
}
if (-not $SevenZip) {
    throw "7-Zip not found. Install it from https://www.7-zip.org/ (needed to extract the Tesseract installer)."
}

function Test-StagedTesseract {
    param([string]$Dir)
    # Verifies the DLLs load and the language data is readable in one call.
    & (Join-Path $Dir "tesseract.exe") "--tessdata-dir" (Join-Path $Dir "tessdata") "--list-langs" 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

# --- Stage Tesseract into build\tesseract (skipped when already staged) ---
if (-not (Test-Path -LiteralPath (Join-Path $TesseractDir "tesseract.exe"))) {
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

    if (-not (Test-Path -LiteralPath $InstallerPath)) {
        Write-Host "Downloading Tesseract installer: $TesseractUrl"
        Invoke-WebRequest -Uri $TesseractUrl -OutFile $InstallerPath
    }

    if (Test-Path -LiteralPath $ExtractDir) { Remove-Item -LiteralPath $ExtractDir -Recurse -Force }
    Write-Host "Extracting Tesseract installer with 7-Zip..."
    & $SevenZip x -y "-o$ExtractDir" $InstallerPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to extract the Tesseract installer." }

    if (Test-Path -LiteralPath $TesseractDir) { Remove-Item -LiteralPath $TesseractDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $TessdataDir | Out-Null

    Copy-Item -LiteralPath (Join-Path $ExtractDir "tesseract.exe") -Destination $TesseractDir
    foreach ($dll in $RuntimeDlls) {
        Copy-Item -LiteralPath (Join-Path $ExtractDir $dll) -Destination $TesseractDir
    }

    # The installer no longer ships language data; download it from the
    # official tessdata repository (fast or best model).
    $TessdataRepo = if ($TessdataModel -eq "best") { "tessdata" } else { "tessdata_fast" }
    $EngDataUrl = "https://github.com/tesseract-ocr/$TessdataRepo/raw/main/eng.traineddata"
    Write-Host "Downloading English language data ($TessdataModel model)..."
    Invoke-WebRequest -Uri $EngDataUrl -OutFile (Join-Path $TessdataDir "eng.traineddata")

    if (-not (Test-StagedTesseract $TesseractDir)) {
        Write-Host "Smoke test failed with the pruned DLL set; falling back to all DLLs."
        Get-ChildItem -LiteralPath $ExtractDir -Filter "*.dll" | Copy-Item -Destination $TesseractDir
        if (-not (Test-StagedTesseract $TesseractDir)) {
            throw "Staged tesseract failed the smoke test (DLLs or language data)."
        }
    }

    Remove-Item -LiteralPath $ExtractDir -Recurse -Force
    Write-Host "Tesseract staged at $TesseractDir"
} else {
    Write-Host "Tesseract already staged at $TesseractDir"
}

# --- Run PyInstaller ---
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    throw "PyInstaller not found. Run: .venv\Scripts\pip install -e '.[dev]'"
}
Write-Host "Running PyInstaller..."
& $PyInstaller --noconfirm --clean (Join-Path $Root "wosutil.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
Write-Host "Done: $(Join-Path $Root 'dist\WosUtil.exe')"
