[CmdletBinding()]
param(
    [switch]$SkipSmoke,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$specPath = Join-Path $projectRoot "deploy\windows\gis_desktop.spec"
$installerScript = Join-Path $projectRoot "deploy\windows\installer.iss"
$distRoot = Join-Path $projectRoot "dist"
$workRoot = Join-Path $projectRoot "build"
$applicationDirectory = Join-Path $distRoot "GISDesktop"
$applicationExe = Join-Path $applicationDirectory "GISDesktop.exe"
$smokeReport = Join-Path $distRoot "packaging-smoke.json"
$pyInstallerExe = Join-Path $projectRoot ".venv\Scripts\pyinstaller.exe"

function Remove-PreviousApplicationDirectory {
    if (-not (Test-Path -LiteralPath $applicationDirectory)) {
        return
    }

    $normalizedDistRoot = [IO.Path]::GetFullPath($distRoot).TrimEnd('\') + '\'
    $normalizedTarget = [IO.Path]::GetFullPath($applicationDirectory)
    if (-not $normalizedTarget.StartsWith($normalizedDistRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理 dist 目录之外的路径：$normalizedTarget"
    }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $normalizedTarget -Recurse -Force
            return
        } catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Seconds 1
        }
    }
}

if (-not (Test-Path -LiteralPath $pyInstallerExe)) {
    throw "未找到 $pyInstallerExe，请先在项目根目录执行 uv sync。"
}

$pyprojectContent = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
$versionMatch = [regex]::Match($pyprojectContent, '(?m)^version\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "无法从 pyproject.toml 读取项目版本。"
}
$applicationVersion = $versionMatch.Groups[1].Value

Write-Host "[1/3] 构建 PyInstaller onedir 发布目录..."
Remove-PreviousApplicationDirectory
& $pyInstallerExe `
    --noconfirm `
    --clean `
    --distpath $distRoot `
    --workpath $workRoot `
    $specPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $applicationExe)) {
    throw "PyInstaller 构建失败。"
}

if (-not $SkipSmoke) {
    Write-Host "[2/3] 运行冻结程序 GIS 原生依赖自检..."
    Remove-Item -LiteralPath $smokeReport -Force -ErrorAction SilentlyContinue
    $smokeProcess = Start-Process `
        -FilePath $applicationExe `
        -ArgumentList @("--packaging-smoke-test", $smokeReport) `
        -Wait `
        -PassThru
    if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $smokeReport)) {
        throw "冻结程序自检失败，退出码：$($smokeProcess.ExitCode)。"
    }
    $smokePayload = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json
    if ($smokePayload.status -ne "ok") {
        throw "冻结程序自检未通过，请查看 $smokeReport。"
    }
} else {
    Write-Host "[2/3] 已跳过冻结程序自检。"
}

if ($SkipInstaller) {
    Write-Host "[3/3] 已跳过安装程序构建。"
    Write-Host "发布目录：$applicationDirectory"
    exit 0
}

$isccCandidates = @(
    (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
$isccExe = $isccCandidates | Select-Object -First 1
if (-not $isccExe) {
    throw "未找到 Inno Setup 6。请安装后重新运行脚本，或暂时使用 -SkipInstaller。"
}

Write-Host "[3/3] 构建 Inno Setup 安装程序..."
$installerOutputDirectory = Join-Path $distRoot "installer"
$installerStagingDirectory = Join-Path ([IO.Path]::GetTempPath()) "GISDesktopInstaller-$PID"
New-Item -ItemType Directory -Path $installerStagingDirectory -Force | Out-Null
& $isccExe `
    "/Qp" `
    "/O$installerStagingDirectory" `
    "/DSourceDir=$applicationDirectory" `
    "/DAppVersion=$applicationVersion" `
    $installerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup 构建失败。"
}

$stagedInstallerPath = Join-Path $installerStagingDirectory "GISDesktop-Setup-$applicationVersion.exe"
$installerPath = Join-Path $installerOutputDirectory "GISDesktop-Setup-$applicationVersion.exe"
New-Item -ItemType Directory -Path $installerOutputDirectory -Force | Out-Null
Copy-Item -LiteralPath $stagedInstallerPath -Destination $installerPath -Force
if (-not (Test-Path -LiteralPath $installerPath)) {
    throw "安装程序未生成：$installerPath"
}
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$installerPath.sha256"
"$installerHash  $(Split-Path -Leaf $installerPath)" | Set-Content `
    -LiteralPath $checksumPath `
    -Encoding ascii
$normalizedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
$normalizedStagingDirectory = [IO.Path]::GetFullPath($installerStagingDirectory)
if ($normalizedStagingDirectory.StartsWith($normalizedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $normalizedStagingDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "安装程序：$installerPath"
Write-Host "SHA256：$checksumPath"
