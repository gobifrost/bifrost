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
    Overwrite an existing .env without prompting. Note: this regenerates all
    secrets (POSTGRES_PASSWORD, RABBITMQ_PASSWORD, SEAWEEDFS_SECRET_KEY,
    BIFROST_SECRET_KEY), so an existing deployment's credentials will rotate.

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
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        while ($result.Length -lt $Length) {
            $bytes = [byte[]]::new($Length * 2)
            $rng.GetBytes($bytes)
            $b64 = [Convert]::ToBase64String($bytes)
            $result += ($b64 -replace '[^A-Za-z0-9]', '')
        }
    } finally {
        $rng.Dispose()
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
        [Parameter(Mandatory)][object[]]$Lines,
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
    # Unary comma: return as an array so a single-element result isn't unwrapped to a scalar.
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
        [Parameter(Mandatory)][object[]]$Lines,
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

    # --- Overwrite guard ---
    if ((Test-Path $envFile) -and -not $Force) {
        $confirm = Read-Host '.env already exists. Overwrite? (y/N)'
        if ($confirm -ne 'y' -and $confirm -ne 'Y') {
            Write-Host 'Setup cancelled.'
            return 0
        }
    }

    # --- Domain ---
    if (-not $Domain) {
        $entered = Read-Host 'Enter your domain (e.g., localhost, app.example.com) [localhost]'
        $Domain = if ([string]::IsNullOrWhiteSpace($entered)) { 'localhost' } else { $entered.Trim() }
    }
    if ($Domain -match '\s' -or $Domain -match '^https?://') {
        Write-Error "Invalid domain '$Domain'. Use a bare host like 'localhost' or 'app.example.com' (no scheme, no spaces)."
        return 1
    }
    $derived = Get-BifrostOrigin -Domain $Domain
    $origin = $derived.Origin
    $environment = $derived.Environment

    Write-Host ''
    Write-Host 'Using:'
    Write-Host "  Domain (RP ID): $Domain"
    Write-Host "  Origin: $origin"
    Write-Host "  Environment: $environment"
    Write-Host ''

    # --- Secrets ---
    $postgresPass = New-BifrostSecret -Length 24
    $rabbitmqPass = New-BifrostSecret -Length 24
    $seaweedfsSecret = New-BifrostSecret -Length 24
    $secretKey = New-BifrostSecret -Length 48

    # --- Assemble .env from .env.example ---
    $lines = Get-Content $envExample
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'POSTGRES_PASSWORD'       -Value $postgresPass
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'RABBITMQ_PASSWORD'       -Value $rabbitmqPass
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'SEAWEEDFS_SECRET_KEY'    -Value $seaweedfsSecret
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_SECRET_KEY'      -Value $secretKey
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_WEBAUTHN_RP_ID'  -Value $Domain
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_WEBAUTHN_ORIGIN' -Value $origin
    $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_ENVIRONMENT'     -Value $environment
    if ($Domain -ne 'localhost' -and $Domain -ne '127.0.0.1') {
        $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_PUBLIC_URL'            -Value "https://$Domain"
        $lines = Set-BifrostEnvLine -Lines $lines -Key 'BIFROST_S3_PUBLIC_ENDPOINT_URL' -Value '/s3'
    }
    Write-BifrostEnvFile -Lines $lines -Path $envFile

    Write-Host '  Created .env with secure secrets'
    Write-Host ''
    Write-Host 'Generated:'
    Write-Host '  - POSTGRES_PASSWORD (24 chars)'
    Write-Host '  - RABBITMQ_PASSWORD (24 chars)'
    Write-Host '  - SEAWEEDFS_SECRET_KEY (24 chars)'
    Write-Host '  - BIFROST_SECRET_KEY (48 chars)'
    Write-Host ''
    Write-Host 'Configured:'
    Write-Host "  - BIFROST_WEBAUTHN_RP_ID=$Domain"
    Write-Host "  - BIFROST_WEBAUTHN_ORIGIN=$origin"
    Write-Host "  - BIFROST_ENVIRONMENT=$environment"
    if ($Domain -ne 'localhost' -and $Domain -ne '127.0.0.1') {
        Write-Host "  - BIFROST_PUBLIC_URL=https://$Domain"
        Write-Host '  - BIFROST_S3_PUBLIC_ENDPOINT_URL=/s3'
    }
    Write-Host ''

    # --- Launch ---
    if ($NoStart) {
        Write-Host 'Skipping launch (-NoStart). To start later:  docker compose up -d'
    } else {
        Write-Host 'Starting Bifrost (docker compose up -d)...'
        & docker compose up -d
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'docker compose up -d failed. Check the output above.'
            return 1
        }
    }

    # --- Instructions ---
    $accessUrl = if ($Domain -eq 'localhost' -or $Domain -eq '127.0.0.1') { 'http://localhost:3000' } else { "https://$Domain" }
    Write-Host ''
    Write-Host 'Next steps:'
    Write-Host "  - Access the platform at: $accessUrl"
    Write-Host '  - View logs:              docker compose logs -f'
    Write-Host '  - Stop the stack:         docker compose down'
    Write-Host ''
    return 0
}

exit (Initialize-Bifrost -Domain $Domain -Force:$Force -NoStart:$NoStart)
