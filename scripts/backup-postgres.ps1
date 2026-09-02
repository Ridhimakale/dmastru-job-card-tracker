param(
  [Parameter(Mandatory=$true)][string]$DatabaseUrl,
  [Parameter(Mandatory=$true)][string]$BackupDirectory
)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Force -Path $BackupDirectory | Out-Null
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$target=Join-Path $BackupDirectory "jobcard-$stamp.dump"
pg_dump --format=custom --file=$target $DatabaseUrl
Get-FileHash -Algorithm SHA256 -LiteralPath $target | Out-File "$target.sha256" -Encoding ascii
Write-Output "Backup created: $target"
