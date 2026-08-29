param(
  [string]$Executable = "",
  [string]$ExpectedVersion = "2.3.5",
  [int]$Port = 8792
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $Executable) {
  $distRoot = Join-Path $root "dist"
  $Executable = Get-ChildItem -LiteralPath $distRoot -Directory |
    ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter "*.exe" -File } |
    Select-Object -First 1 -ExpandProperty FullName
}
$exePath = [IO.Path]::GetFullPath($Executable)
if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
  throw "Frozen executable not found: $exePath"
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
  if ($process.HasExited) { throw "Frozen executable exited early: $($process.ExitCode)" }
  if ($null -eq $health -or $health.version -ne $ExpectedVersion) {
    throw "Frozen executable did not pass the health check in time"
  }
  Write-Host "FROZEN_SMOKE_OK version=$($health.version) pid=$($process.Id)"
} finally {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit(5000)
  }
}
