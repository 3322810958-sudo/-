param(
  [string]$Version = "2.4.1",
  [string]$PythonPath = "",
  [switch]$SkipBuild,
  [switch]$SkipTutorialPdf,
  [switch]$PatchOnly
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$target = Join-Path $root "dist\燕翔车队经费管理系统"
$releaseDir = Join-Path $root "release"
$localPython = Join-Path $root ".venv\Scripts\python.exe"
if ($PythonPath) {
  $python = [IO.Path]::GetFullPath($PythonPath)
} elseif (Test-Path -LiteralPath $localPython -PathType Leaf) {
  $python = $localPython
} else {
  $python = (Get-Command python -ErrorAction Stop).Source
}

Set-Location -LiteralPath $root
if (-not $SkipBuild) {
  if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "缺少 Python 构建环境，请先运行 INSTALL_WINDOWS.cmd"
  }
  & $python -m PyInstaller --noconfirm --clean YanxiangExpenseV2.spec
  if ($LASTEXITCODE -ne 0) { throw "Windows 软件构建失败" }
}
if (-not (Test-Path -LiteralPath (Join-Path $target "燕翔车队经费管理系统.exe") -PathType Leaf)) {
  throw "未找到构建后的主程序：$target"
}

$webTarget = Join-Path $target "web"
if (Test-Path -LiteralPath $webTarget) { Remove-Item -LiteralPath $webTarget -Recurse -Force }
Copy-Item -LiteralPath (Join-Path $root "app\static") -Destination $webTarget -Recurse -Force

if (Test-Path -LiteralPath (Join-Path $root "models")) {
  Copy-Item -LiteralPath (Join-Path $root "models") -Destination (Join-Path $target "models") -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $root "README_FIRST.txt") -Destination $target -Force
Copy-Item -LiteralPath (Join-Path $root "CHANGELOG.md") -Destination $target -Force

$guideDir = Join-Path $target "使用教程"
New-Item -ItemType Directory -Path $guideDir -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $root "docs\运行与使用说明.md") -Destination $guideDir -Force
Copy-Item -LiteralPath (Join-Path $root "docs\补丁安装说明.txt") -Destination $guideDir -Force
if ((-not $SkipTutorialPdf) -and (Test-Path -LiteralPath $python)) {
  & $python scripts\create_tutorial_pdf.py
  if ($LASTEXITCODE -ne 0) { throw "PDF 使用教程生成失败" }
}
$existingGuide = Join-Path $root "output\pdf\燕翔车队经费管理系统_V2.2_使用教程.pdf"
if (Test-Path -LiteralPath $existingGuide) { Copy-Item -LiteralPath $existingGuide -Destination $guideDir -Force }

New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
$fullZip = Join-Path $releaseDir "燕翔车队经费管理系统_V$Version`_Windows完整版.zip"
$updateZip = Join-Path $releaseDir "燕翔车队经费管理系统_V$Version`_WindowsUpdate无损升级补丁.zip"
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$patchStage = Join-Path $tempRoot "yxrt-update-stage-$PID-$Version"

$outputsToReplace = @($updateZip, "$updateZip.sha256", $fullZip, "$fullZip.sha256")
foreach ($path in $outputsToReplace) {
  if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
if (Test-Path -LiteralPath $patchStage) {
  $resolvedStage = [IO.Path]::GetFullPath($patchStage)
  if (-not $resolvedStage.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or -not ([IO.Path]::GetFileName($resolvedStage)).StartsWith("yxrt-update-stage-")) {
    throw "补丁临时目录不安全"
  }
  Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}

if (-not $PatchOnly) {
  Compress-Archive -Path (Join-Path $target "*") -DestinationPath $fullZip -CompressionLevel Optimal
}
New-Item -ItemType Directory -Path $patchStage -Force | Out-Null
# The executable and _internal runtime are one matched PyInstaller build.
# Replacing only the EXE can leave it paired with an incompatible Python runtime.
$deltaItems = @("燕翔车队经费管理系统.exe", "_internal", "web", "README.md", "README_FIRST.txt", "CHANGELOG.md")
foreach ($name in $deltaItems) {
  $item = Join-Path $target $name
  if (Test-Path -LiteralPath $item) { Copy-Item -LiteralPath $item -Destination (Join-Path $patchStage $name) -Recurse -Force }
}
Copy-Item -LiteralPath (Join-Path $root "docs\补丁安装说明.txt") -Destination $patchStage -Force
$manifestFiles = @()
foreach ($file in Get-ChildItem -LiteralPath $patchStage -File -Recurse) {
  $relative = $file.FullName.Substring($patchStage.Length).TrimStart('\','/').Replace('\','/')
  $manifestFiles += [ordered]@{
    path = $relative
    size = $file.Length
    sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}
$patchManifest = [ordered]@{
  product = "燕翔车队经费管理系统"
  format_version = 1
  target_version = $Version
  compatible_from = "any-v2"
  data_policy = "preserve-or-restore-verified-backup"
  runtime_bundle = "matched-pyinstaller-runtime"
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  files = $manifestFiles
}
$patchManifest | ConvertTo-Json -Depth 6 | Out-File -LiteralPath (Join-Path $patchStage "patch-manifest.json") -Encoding utf8
Compress-Archive -Path (Join-Path $patchStage "*") -DestinationPath $updateZip -CompressionLevel Optimal
Remove-Item -LiteralPath $patchStage -Recurse -Force

$packagesToHash = @($updateZip)
if (-not $PatchOnly) { $packagesToHash += $fullZip }
foreach ($package in $packagesToHash) {
  $hash = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
  "$hash  $([IO.Path]::GetFileName($package))" | Out-File -LiteralPath "$package.sha256" -Encoding utf8
}

Write-Host "已生成："
if (-not $PatchOnly) { Write-Host $fullZip }
Write-Host $updateZip
