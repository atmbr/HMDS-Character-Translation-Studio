$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$Version = "0.10.0"
$ExeName = "HMDS_Character_Translation_Studio_v$Version.exe"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " HMDS Character Translation Studio - Windows EXE Builder" -ForegroundColor Cyan
Write-Host " Compiler: MSVC (Zig is intentionally disabled)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Do not let CC/CXX force Zig/Clang.
Remove-Item Env:CC -ErrorAction SilentlyContinue
Remove-Item Env:CXX -ErrorAction SilentlyContinue
Remove-Item Env:NUITKA_CC -ErrorAction SilentlyContinue

function Test-Python313 {
    try {
        & py -3.13 -c "import sys; assert sys.version_info[:2] == (3,13); print(sys.executable)" | Out-Null
        return $true
    } catch { return $false }
}

function Get-MSVCPath {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        try {
            $path = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            if ($LASTEXITCODE -eq 0 -and ![string]::IsNullOrWhiteSpace($path)) {
                return $path.Trim()
            }
        } catch {}
    }

    # Fallback for the standard Build Tools install path.
    $known = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2022\BuildTools"
    if (Test-Path $known) {
        $cl = Get-ChildItem -Path (Join-Path $known "VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe") -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cl) { return $known }
    }
    return $null
}

function Install-MSVCBuildTools {
    Write-Host "MSVC C++ Build Tools nao foram encontrados." -ForegroundColor Yellow
    Write-Host "Baixando o instalador oficial da Microsoft..." -ForegroundColor Yellow

    $cache = Join-Path $PSScriptRoot "_cache"
    New-Item -ItemType Directory -Force -Path $cache | Out-Null
    $bootstrapper = Join-Path $cache "vs_BuildTools.exe"
    $url = "https://aka.ms/vs/17/release/vs_BuildTools.exe"

    if (!(Test-Path $bootstrapper)) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $bootstrapper
        } catch {
            throw "Nao foi possivel baixar o Visual Studio Build Tools. Verifique a internet e tente novamente. Detalhes: $($_.Exception.Message)"
        }
    }

    Write-Host "O instalador oficial sera aberto com permissao de administrador." -ForegroundColor Yellow
    Write-Host "A instalacao usa o workload Desktop development with C++ / VCTools." -ForegroundColor Yellow

    # Direct bootstrapper avoids WinGet command-line parsing differences between versions.
    $vsArgs = "--passive --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    $proc = Start-Process -FilePath $bootstrapper -ArgumentList $vsArgs -Verb RunAs -Wait -PassThru

    # 3010 = success, reboot recommended/required.
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        throw "Visual Studio Build Tools falhou (codigo $($proc.ExitCode)). Abra o Visual Studio Installer e instale 'Desktop development with C++', depois execute este script novamente."
    }

    if ($proc.ExitCode -eq 3010) {
        Write-Host "Build Tools instalados. O Windows recomendou reiniciar; tentarei continuar primeiro." -ForegroundColor Yellow
    }

    Start-Sleep -Seconds 3
}

if (!(Test-Python313)) {
    Write-Host "Python 3.13 x64 nao foi encontrado." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Instalando Python 3.13 pelo winget..." -ForegroundColor Yellow
        & winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
    }
    if (!(Test-Python313)) {
        throw "Python 3.13 nao esta disponivel. Instale o Python 3.13 x64 e execute novamente."
    }
}

$msvc = Get-MSVCPath
if (!$msvc) {
    Install-MSVCBuildTools
    $msvc = Get-MSVCPath
}
if (!$msvc) {
    throw "MSVC ainda nao foi detectado depois da instalacao. Reinicie o Windows uma vez e execute COMPILAR_EXE.ps1 novamente."
}
Write-Host "MSVC encontrado: $msvc" -ForegroundColor Green

Write-Host "[1/4] Instalando dependencias de build..." -ForegroundColor Cyan
& py -3.13 -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar pip." }
& py -3.13 -m pip install --upgrade "Nuitka==4.2.1" ordered-set zstandard Pillow
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar Nuitka/Pillow." }

Write-Host "[2/4] Verificando ambiente..." -ForegroundColor Cyan
& py -3.13 -c "import tkinter, PIL; print('Python/Tk/Pillow OK')"
if ($LASTEXITCODE -ne 0) { throw "Python/Tk/Pillow nao passaram na verificacao." }
& py -3.13 -m nuitka --version
if ($LASTEXITCODE -ne 0) { throw "Nuitka nao passou na verificacao." }

Write-Host "[3/4] Compilando com MSVC..." -ForegroundColor Cyan
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }

& py -3.13 -m nuitka `
  --mode=onefile `
  --msvc=latest `
  --lto=no `
  --assume-yes-for-downloads `
  --remove-output `
  --python-flag=no_docstrings `
  --windows-console-mode=disable `
  --enable-plugin=tk-inter `
  --include-package=PIL `
  --include-data-dir=cts\data=cts\data `
  --windows-icon-from-ico=cts\data\app_icon.ico `
  --company-name=Atm `
  --product-name="HMDS Character Translation Studio" `
  --file-description="Harvest Moon DS fan-translation utility" `
  --file-version=0.10.0.0 `
  --product-version=0.10.0.0 `
  --copyright="Copyright 2026 Atm" `
  --output-dir=dist `
  --output-filename=$ExeName `
  HMDS_Character_Translation_Studio.pyw

if ($LASTEXITCODE -ne 0) { throw "Nuitka falhou com codigo $LASTEXITCODE." }

$exe = Join-Path $PWD "dist\$ExeName"
if (!(Test-Path $exe)) { throw "A compilacao terminou, mas o EXE nao foi encontrado em $exe" }

Write-Host "[4/4] Concluido." -ForegroundColor Green
Write-Host "EXE:" -ForegroundColor Green
Write-Host $exe -ForegroundColor White
Start-Process explorer.exe -ArgumentList "/select,`"$exe`""
