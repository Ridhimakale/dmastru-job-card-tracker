param(
  [string]$Url='http://127.0.0.1:8000/health',
  [ValidateRange(1,10000)][int]$Requests=100,
  [ValidateRange(1,50)][int]$Concurrency=10
)
$ErrorActionPreference='Stop'
$jobs=1..$Concurrency | ForEach-Object {
  $count=[math]::Ceiling($Requests/$Concurrency)
  Start-Job -ArgumentList $Url,$count -ScriptBlock {
    param($target,$iterations)
    1..$iterations | ForEach-Object {
      try {(Invoke-WebRequest -UseBasicParsing -Uri $target -TimeoutSec 10).StatusCode}
      catch {0}
    }
  }
}
$codes=$jobs | Wait-Job | Receive-Job
$jobs | Remove-Job
$success=($codes | Where-Object {$_ -eq 200}).Count
Write-Output "Successful health requests: $success / $($codes.Count)"
if($success -ne $codes.Count){exit 1}
