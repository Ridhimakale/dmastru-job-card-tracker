param(
  [Parameter(Mandatory=$true)][string]$DatabaseUrl,
  [Parameter(Mandatory=$true)][string]$BackupFile
)
$ErrorActionPreference='Stop'
if(-not (Test-Path -LiteralPath $BackupFile)){throw "Backup file does not exist."}
$checksumFile="$BackupFile.sha256"
if(Test-Path -LiteralPath $checksumFile){
  $expected=(Get-Content -LiteralPath $checksumFile -Raw).Trim().Split()[0]
  $actual=(Get-FileHash -Algorithm SHA256 -LiteralPath $BackupFile).Hash
  if($expected -ne $actual){throw "Backup checksum verification failed."}
}
pg_restore --clean --if-exists --no-owner --dbname=$DatabaseUrl $BackupFile
Write-Output "Restore complete. Run health checks before reopening the application."
