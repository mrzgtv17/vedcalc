# Сборка для Windows: PyInstaller, установщик Inno Setup, проверка.
# Использование: scripts/build_windows.ps1 [путь к python, по умолчанию python]
# Проверка: установщик ставит программу в отдельную папку, установленная программа
# считает контрольный пример и выходит с кодом 0 (VEDCALC_SMOKE_TEST).
param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$version = & $Python -c "import vedcalc; print(vedcalc.__version__)"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean scripts/vedcalc.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller: код $LASTEXITCODE" }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    choco install innosetup -y --no-progress
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить Inno Setup" }
}
& $iscc "/DAppVersion=$version" scripts\vedcalc.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup: код $LASTEXITCODE" }

$setup = "dist\vedcalc-$version-windows-x64-setup.exe"
$dir = Join-Path $env:RUNNER_TEMP "vedcalc-install"
if (-not $env:RUNNER_TEMP) { $dir = Join-Path $env:TEMP "vedcalc-install" }
$p = Start-Process -FilePath $setup -Wait -PassThru `
    -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOLAUNCH=1", "/DIR=$dir"
if ($p.ExitCode -ne 0) { throw "Установщик: код $($p.ExitCode)" }

$env:VEDCALC_SMOKE_TEST = Join-Path $dir "smoke.png"
$p = Start-Process -FilePath (Join-Path $dir "vedcalc.exe") -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Контрольный пример: код $($p.ExitCode)" }
Write-Host "Готово: $setup"
