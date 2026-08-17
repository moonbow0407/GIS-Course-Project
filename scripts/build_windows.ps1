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

function Set-MinimalBuildPath {
    # PyInstaller 做二进制依赖分析时会搜索 PATH 上的每个目录。JDK、conda、
    # Git 等软件的 bin 目录里带有旧版 UCRT 存根（api-ms-win-*.dll、
    # ucrtbase.dll）和同名 OpenSSL，一旦被收进发布包，冻结程序启动时会按
    # 目录优先级先加载这些过期 DLL，因缺少新版导出函数导致 Qt 报
    # “找不到指定的程序”。黑名单逐个排除不可靠，改为白名单：依赖解析只需
    # 要系统目录和 venv。原 PATH 保存到 $script:originalPath 供后续恢复。
    $script:originalPath = $env:PATH
    $allowedRoots = @(
        (Join-Path $env:SystemRoot "System32"),
        $env:SystemRoot,
        (Join-Path $env:SystemRoot "System32\Wbem"),
        (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0"),
        (Join-Path $projectRoot ".venv\Scripts")
    )

    $keptEntries = @()
    foreach ($entry in ($env:PATH -split ';')) {
        if (-not $entry) {
            continue
        }
        try {
            $fullEntry = [IO.Path]::GetFullPath($entry).TrimEnd('\')
        } catch {
            $fullEntry = $entry
        }

        $allowed = $false
        foreach ($root in $allowedRoots) {
            $normalizedRoot = $root.TrimEnd('\')
            if (
                $fullEntry.Equals($normalizedRoot, [StringComparison]::OrdinalIgnoreCase) -or
                $fullEntry.StartsWith($normalizedRoot + '\', [StringComparison]::OrdinalIgnoreCase)
            ) {
                $allowed = $true
                break
            }
        }
        if ($allowed) {
            $keptEntries += $entry
        } else {
            Write-Host "构建期间已从 PATH 移除：$fullEntry"
        }
    }
    $env:PATH = ($keptEntries | Select-Object -Unique) -join ';'
}

function Assert-NoForeignUcrtInPackage {
    # Win10+ 的 API-Set 与 UCRT 是系统内存中的虚拟 DLL，正常打包不会以实体
    # 文件出现；一旦出现说明外部目录（JDK、conda 等）经 PATH 污染了依赖分析。
    $internalDirectory = Join-Path $applicationDirectory "_internal"
    $foreignFiles = @()
    if (Test-Path -LiteralPath $internalDirectory) {
        $foreignFiles = @(
            Get-ChildItem -LiteralPath $internalDirectory -Recurse -Filter "api-ms-win-*.dll"
            Get-ChildItem -LiteralPath $internalDirectory -Recurse -Filter "ucrtbase.dll"
        )
    }
    if ($foreignFiles.Count -gt 0) {
        $sampleNames = ($foreignFiles | Select-Object -First 3 | ForEach-Object { $_.Name }) -join "、"
        throw "发布包混入了 $sampleNames 等 $($foreignFiles.Count) 个 UCRT 存根文件，冻结程序将无法启动；请检查构建期间的 PATH 白名单。"
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
Set-MinimalBuildPath
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
Assert-NoForeignUcrtInPackage
$env:PATH = $script:originalPath

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
