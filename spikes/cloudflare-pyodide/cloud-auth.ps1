# Generate only this disposable probe's secret file. Never prints the value.
$ErrorActionPreference = 'Stop'
$spikeSecretPath = Join-Path $env:TEMP 'bifrost-pyodide-ddf316c1-secrets.json'
$spikeBytes = New-Object byte[] 32
$spikeRng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $spikeRng.GetBytes($spikeBytes) } finally { $spikeRng.Dispose() }
$spikeSecretJson = @{ SPIKE_AUTH = [Convert]::ToBase64String($spikeBytes) } | ConvertTo-Json
[System.IO.File]::WriteAllText($spikeSecretPath, $spikeSecretJson)
Write-Output 'Generated the isolated probe authentication file in the OS temp directory.'
