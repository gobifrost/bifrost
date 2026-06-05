#Requires -Version 5.1
<#
.SYNOPSIS
    Initialize a Bifrost deployment on Windows: generate .env, launch the
    stack, and print access instructions.

.DESCRIPTION
    PowerShell-native counterpart to setup.sh for Windows deploy boxes. Has no
    dependency on openssl, sed, Git Bash, or WSL. Run from the repository root.

.PARAMETER Domain
    Domain / WebAuthn RP ID. Defaults to "localhost" (prompted if omitted).

.PARAMETER Force
    Overwrite an existing .env without prompting.

.PARAMETER NoStart
    Generate .env but do not run `docker compose up -d`.

.EXAMPLE
    .\Initialize-Bifrost.ps1

.EXAMPLE
    .\Initialize-Bifrost.ps1 -Domain app.example.com -Force
#>
[CmdletBinding()]
param(
    [string]$Domain,
    [switch]$Force,
    [switch]$NoStart
)

function Initialize-Bifrost {
    [CmdletBinding()]
    param(
        [string]$Domain,
        [switch]$Force,
        [switch]$NoStart
    )

    $ErrorActionPreference = 'Stop'
    $envFile = '.env'
    $envExample = '.env.example'

    Write-Host 'Bifrost Setup'
    Write-Host '============='
    Write-Host ''

    # --- Preflight: Docker must be installed and running ---
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error 'Docker is not on PATH. Install Docker Desktop and retry.'
        return 1
    }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'Docker is installed but the daemon is not responding. Start Docker Desktop and retry.'
        return 1
    }

    # --- Preflight: .env.example must exist ---
    if (-not (Test-Path $envExample)) {
        Write-Error "$envExample not found. Run this from the repository root."
        return 1
    }

    Write-Host 'Preflight OK (Docker running, .env.example present).'
    return 0
}

exit (Initialize-Bifrost -Domain $Domain -Force:$Force -NoStart:$NoStart)
