$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $Root "qgc-custom\build\release-msvc\staging"
$DistRoot = Join-Path $Root "dist"
$PkgName = "Gimli-QGC-Rover"
$Pkg = Join-Path $DistRoot $PkgName
$Zip = Join-Path $DistRoot "Gimli-QGC-Rover-2026-06-01.zip"
$InstallerWork = Join-Path $DistRoot "installer-work"
$InstallerArchive = Join-Path $DistRoot "gimli-qgc-installer.7z"
$InstallerConfig = Join-Path $DistRoot "gimli-qgc-sfx-config.txt"
$InstallerExe = Join-Path $DistRoot "Gimli-QGC-Rover-Setup-2026-06-01.exe"

if (!(Test-Path (Join-Path $Stage "bin\QGroundControl.exe"))) {
    throw "Release staging folder is not ready. Expected $Stage\bin\QGroundControl.exe"
}

if (Test-Path $Pkg) { Remove-Item -LiteralPath $Pkg -Recurse -Force }
if (Test-Path $InstallerWork) { Remove-Item -LiteralPath $InstallerWork -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Pkg, $InstallerWork | Out-Null

foreach ($name in @("bin", "lib", "libexec", "plugins", "qml", "translations", "share")) {
    $src = Join-Path $Stage $name
    if (Test-Path $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $Pkg $name) -Recurse
    }
}

@'
@echo off
setlocal
cd /d "%~dp0bin"
set "GIMLI_ROOT=%~dp0"
set "PATH=%CD%;%GIMLI_ROOT%libexec\gstreamer-1.0;%PATH%"
set "GST_PLUGIN_PATH=%GIMLI_ROOT%lib\gstreamer-1.0"
set "GST_PLUGIN_SYSTEM_PATH=%GIMLI_ROOT%lib\gstreamer-1.0"
set "GST_PLUGIN_SCANNER=%GIMLI_ROOT%libexec\gstreamer-1.0\gst-plugin-scanner.exe"
set "GST_REGISTRY=%GIMLI_ROOT%gst-registry.bin"
set "GST_PLUGIN_FEATURE_RANK=avdec_h264:MAX,avdec_h265:MAX,d3d11h264dec:NONE,d3d11h265dec:NONE,d3d12h264dec:NONE,d3d12h265dec:NONE,nvh264dec:NONE,nvh265dec:NONE,nvav1dec:NONE,d3d11videosink:NONE,d3d12videosink:NONE"
start "" "%CD%\QGroundControl.exe"
'@ | Set-Content -LiteralPath (Join-Path $Pkg "Start-Gimli-QGC.cmd") -Encoding ASCII

@'
Gimli Rover QGroundControl portable build
========================================

Run:
1. Use Start-Gimli-QGC.cmd.
2. If Windows SmartScreen appears, choose More info / Run anyway.

Rover link:
- Comm Link: TCP
- Host: gimli-rover.tailfd4169.ts.net
- Port: 5760

Video:
- Source: RTSP Video Stream
- URL: rtsp://gimli-rover.tailfd4169.ts.net:8554/qgc

Qt runtime and GStreamer runtime are included.
'@ | Set-Content -LiteralPath (Join-Path $Pkg "README-GIMLI.txt") -Encoding UTF8

$InstallCmd = Join-Path $InstallerWork "Install-Gimli-QGC.cmd"
@'
@echo off
setlocal
set "SRC=%~dp0Gimli-QGC-Rover"
set "DST=%LOCALAPPDATA%\Gimli-QGC-Rover"
echo Installing Gimli QGC to "%DST%"...
if exist "%DST%" rmdir /s /q "%DST%"
xcopy "%SRC%" "%DST%\" /E /I /Y >nul
if errorlevel 1 (
  echo Install failed.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Gimli QGC Rover.lnk'); $s.TargetPath=$env:LOCALAPPDATA + '\Gimli-QGC-Rover\Start-Gimli-QGC.cmd'; $s.WorkingDirectory=$env:LOCALAPPDATA + '\Gimli-QGC-Rover'; $s.IconLocation=$env:LOCALAPPDATA + '\Gimli-QGC-Rover\bin\QGroundControl.exe'; $s.Save()"
echo Installed.
start "" "%DST%\Start-Gimli-QGC.cmd"
exit /b 0
'@ | Set-Content -LiteralPath $InstallCmd -Encoding ASCII

Copy-Item -LiteralPath $Pkg -Destination (Join-Path $InstallerWork $PkgName) -Recurse

if (Test-Path $Zip) { Remove-Item -LiteralPath $Zip -Force }
Compress-Archive -LiteralPath $Pkg -DestinationPath $Zip -CompressionLevel Optimal

$SevenZip = "C:\Program Files\7-Zip\7z.exe"
$Sfx = "C:\Program Files\7-Zip\7z.sfx"
if (!(Test-Path $SevenZip) -or !(Test-Path $Sfx)) {
    throw "7-Zip SFX tools not found."
}
if (Test-Path $InstallerArchive) { Remove-Item -LiteralPath $InstallerArchive -Force }
if (Test-Path $InstallerExe) { Remove-Item -LiteralPath $InstallerExe -Force }
& $SevenZip a -t7z -mx=9 $InstallerArchive (Join-Path $InstallerWork "*") | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "7-Zip archive failed with exit code $LASTEXITCODE"
}

@'
;!@Install@!UTF-8!
Title="Gimli QGC Rover Setup"
BeginPrompt="Install Gimli QGC Rover?"
RunProgram="Install-Gimli-QGC.cmd"
GUIMode="1"
;!@InstallEnd@!
'@ | Set-Content -LiteralPath $InstallerConfig -Encoding UTF8

$out = [System.IO.File]::OpenWrite($InstallerExe)
try {
    foreach ($part in @($Sfx, $InstallerConfig, $InstallerArchive)) {
        $bytes = [System.IO.File]::ReadAllBytes($part)
        $out.Write($bytes, 0, $bytes.Length)
    }
} finally {
    $out.Dispose()
}

Get-Item $Zip, $InstallerExe | Select-Object FullName, Length, LastWriteTime
