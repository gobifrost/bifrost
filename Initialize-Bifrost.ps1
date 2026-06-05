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

function New-BifrostSecret {
    <#
        Cryptographically-random alphanumeric secret of the requested length.
        Mirrors setup.sh: base64 of random bytes, stripped to [A-Za-z0-9],
        truncated to $Length. Over-generates bytes so stripping never starves.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][int]$Length)

    $result = ''
    while ($result.Length -lt $Length) {
        $bytes = [byte[]]::new($Length * 2)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $b64 = [Convert]::ToBase64String($bytes)
        $result += ($b64 -replace '[^A-Za-z0-9]', '')
    }
    return $result.Substring(0, $Length)
}

function Get-BifrostOrigin {
    <#
        Derive (Origin, Environment) from a domain, matching setup.sh:
        localhost / 127.0.0.1 -> http://<domain>:3000 + development
        otherwise             -> https://<domain>      + production
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Domain)

    if ($Domain -eq 'localhost' -or $Domain -eq '127.0.0.1') {
        return [pscustomobject]@{ Origin = "http://${Domain}:3000"; Environment = 'development' }
    }
    return [pscustomobject]@{ Origin = "https://${Domain}"; Environment = 'production' }
}

function Set-BifrostEnvLine {
    <#
        Replace the value of KEY=... on the single matching line. Matches an
        optional leading "# " so a commented key can be activated. Anchored to
        line start so it never touches comments that merely mention the key.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Lines,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Value
    )
    $pattern = '^(#\s*)?' + [regex]::Escape($Key) + '=.*$'
    $replacement = "$Key=$Value"
    $done = $false
    $out = foreach ($line in $Lines) {
        if (-not $done -and $line -match $pattern) {
            $done = $true
            $replacement
        } else {
            $line
        }
    }
    return ,$out
}

function Write-BifrostEnvFile {
    <#
        Write .env as UTF-8 WITHOUT BOM and LF line endings. A BOM breaks
        Docker Compose env_file parsing (the first key gets a BOM prefix), and
        Out-File/Set-Content add one by default — hence the explicit writer.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Lines,
        [Parameter(Mandatory)][string]$Path
    )
    $text = ($Lines -join "`n") + "`n"
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText((Join-Path (Get-Location) $Path), $text, $utf8NoBom)
}

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
