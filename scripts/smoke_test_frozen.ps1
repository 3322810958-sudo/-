param(
  [string]$Executable = "",
  [string]$ExpectedVersion = "2.3.2",
  [int]$Port = 8792
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $Executable) {
  $Executable = Join-Path $root "dist\燕翔车队经费管理系统\燕翔车队经费管理系统.exe"
}
$exePath = [IO.Path]::GetFullPath($Executable)
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
  throw "未找到待测试主程序：$exePath"
}

$runtime = Join-Path $root "tmp\frozen-smoke-$ExpectedVersion"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$env:YXRT_HOME = $runtime
$env:YXRT_PORT = [string]$Port
$env:YXRT_OCR_WARMUP = "0"

$process = Start-Process -FilePath $exePath -WindowStyle Hidden -PassThru
try {
  $deadline = [DateTime]::UtcNow.AddSeconds(40)
  $health = $null
  while ([DateTime]::UtcNow -lt $deadline -and -not $process.HasExited) {
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
      if ($health.version -eq $ExpectedVersion) { break }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if ($process.HasExited) { throw "主程序提前退出，退出码：$($process.ExitCode)" }
  if ($null -eq $health -or $health.version -ne $ExpectedVersion) {
    throw "主程序未在限定时间内通过健康检查"
  }
  Write-Host "FROZEN_SMOKE_OK version=$($health.version) pid=$($process.Id)"
} finally {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit(5000)
  }
}
