# Initialize-Bifrost.ps1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PowerShell-native `Initialize-Bifrost.ps1` that an operator runs on a Windows deploy box to generate a production `.env`, launch the stack, and print access instructions — and delete the now-unused `build.sh`.

**Architecture:** A single repo-root script that defines an `Initialize-Bifrost` function (approved verb `Initialize-`) and invokes it at the bottom. The function is a faithful port of `setup.sh` using PowerShell-native crypto/string ops (no openssl/sed/Git Bash), plus three deploy-box guards (Docker running, `.env.example` present, overwrite confirm), a `docker compose up -d` launch step, and an instructions printout. Pure-logic pieces (secret generation, origin derivation, `.env` key rewriting) are written as small helper functions so the flow reads top-down and stays testable.

**Tech Stack:** PowerShell 5.1+ / PowerShell 7, `System.Security.Cryptography.RandomNumberGenerator`, `System.IO.File` (UTF-8-no-BOM writes), Docker Compose.

**Verification:** VM smoke test on `win11-pam` (no Pester this iteration). The spec lives at `docs/superpowers/specs/2026-06-05-initialize-bifrost-ps1-design.md`.

---

## File Structure

- **Create:** `Initialize-Bifrost.ps1` (repo root) — the entire feature. One file, because it's a single linear operator flow; helper functions inside keep responsibilities separated without fragmenting a ~200-line script across files.
- **Delete:** `build.sh` (repo root) — unused manual-release tooling.

Reference (read-only, do not modify): `setup.sh` (the source of truth for the `.env` contract), `.env.example` (the keys to rewrite), `docker-compose.yml` (the default stack `docker compose up -d` targets).

---

### Task 1: Script skeleton, params, and preflight guards

Build the outer shell: `param(...)`, the `Initialize-Bifrost` function definition, the Docker + `.env.example` preflight, and the invocation at the bottom. No secret/`.env` logic yet — this task produces a script that runs, checks prerequisites, and exits cleanly.

**Files:**
- Create: `Initialize-Bifrost.ps1`

- [ ] **Step 1: Write the script skeleton with params and preflight**

Create `Initialize-Bifrost.ps1` with exactly this content:

```powershell
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
```

- [ ] **Step 2: Syntax-check the script parses**

Run (on the Linux host, pwsh if available; otherwise defer to the VM):
```bash
pwsh -NoProfile -Command "Get-Command -Syntax { . ./Initialize-Bifrost.ps1 }" 2>&1 || \
pwsh -NoProfile -Command '$null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path ./Initialize-Bifrost.ps1), [ref]$null, [ref]$null); "parse-ok"'
```
Expected: `parse-ok` (or no parse errors). If `pwsh` is not installed on the host, skip and rely on the VM smoke test in Task 5.

- [ ] **Step 3: Commit**

```bash
git add Initialize-Bifrost.ps1
git commit -m "feat(windows): Initialize-Bifrost.ps1 skeleton + preflight guards"
```

---

### Task 2: Secret generation and origin-derivation helpers

Add the two pure-logic helpers: cryptographically-random alphanumeric secrets (matching the bash lengths) and the domain→origin/environment derivation. These are defined above `Initialize-Bifrost` so the main flow can call them.

**Files:**
- Modify: `Initialize-Bifrost.ps1`

- [ ] **Step 1: Add helper functions above `function Initialize-Bifrost`**

Insert this block immediately after the `param(...)` block and before `function Initialize-Bifrost {`:

```powershell
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
```

- [ ] **Step 2: Verify the helpers behave correctly**

Run (host pwsh if available, else defer to VM):
```bash
pwsh -NoProfile -Command ". ./Initialize-Bifrost.ps1 -NoStart -Domain localhost *> \$null; `
  \$s = New-BifrostSecret -Length 48; `
  if (\$s.Length -ne 48) { throw 'bad length' }; `
  if (\$s -match '[^A-Za-z0-9]') { throw 'non-alnum char' }; `
  \$o = Get-BifrostOrigin -Domain localhost; \$p = Get-BifrostOrigin -Domain app.example.com; `
  if (\$o.Origin -ne 'http://localhost:3000' -or \$o.Environment -ne 'development') { throw 'localhost origin wrong' }; `
  if (\$p.Origin -ne 'https://app.example.com' -or \$p.Environment -ne 'production') { throw 'prod origin wrong' }; `
  'helpers-ok'"
```
Expected: `helpers-ok`. (Dot-sourcing runs `Initialize-Bifrost` via the bottom `exit` line — `-NoStart` keeps it from launching Docker; the preflight may print, that's fine. If dot-sourcing complicates this, factor the bottom `exit` line behind `if ($MyInvocation.InvocationName -ne '.')` in Task 1 — but only if needed.) If `pwsh` is unavailable on the host, defer to the VM smoke test.

- [ ] **Step 3: Commit**

```bash
git add Initialize-Bifrost.ps1
git commit -m "feat(windows): secret + origin helpers for Initialize-Bifrost"
```

---

### Task 3: `.env` rewriting helper (UTF-8 no BOM)

Add the helper that takes the `.env.example` contents plus the computed values and returns the rewritten `.env` text, then a writer that persists it as **UTF-8 without BOM** with LF line endings. The BOM detail is the single most important correctness point in the script.

**Files:**
- Modify: `Initialize-Bifrost.ps1`

- [ ] **Step 1: Add the rewriting + writing helpers**

Insert after `Get-BifrostOrigin` (still above `function Initialize-Bifrost`):

```powershell
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
```

- [ ] **Step 2: Verify rewriting + no-BOM**

Run (host pwsh if available, else defer to VM):
```bash
pwsh -NoProfile -Command ". ./Initialize-Bifrost.ps1 -NoStart -Domain localhost *> \$null; `
  \$lines = @('# header', 'POSTGRES_PASSWORD=bifrost_dev', '# BIFROST_S3_PUBLIC_ENDPOINT_URL=/s3  # comment'); `
  \$lines = Set-BifrostEnvLine -Lines \$lines -Key 'POSTGRES_PASSWORD' -Value 'SECRET123'; `
  \$lines = Set-BifrostEnvLine -Lines \$lines -Key 'BIFROST_S3_PUBLIC_ENDPOINT_URL' -Value '/s3'; `
  if (\$lines -notcontains 'POSTGRES_PASSWORD=SECRET123') { throw 'value not replaced' }; `
  if (\$lines -notcontains 'BIFROST_S3_PUBLIC_ENDPOINT_URL=/s3') { throw 'commented key not activated' }; `
  \$tmp = [System.IO.Path]::GetTempFileName(); `
  Write-BifrostEnvFile -Lines \$lines -Path \$tmp; `
  \$bytes = [System.IO.File]::ReadAllBytes(\$tmp); `
  if (\$bytes[0] -eq 0xEF -and \$bytes[1] -eq 0xBB -and \$bytes[2] -eq 0xBF) { throw 'BOM present!' }; `
  Remove-Item \$tmp; 'env-helpers-ok'"
```
Expected: `env-helpers-ok` (no BOM, value replaced, commented key activated). The `Write-BifrostEnvFile` `Join-Path (Get-Location)` handles the absolute temp path correctly because `Join-Path` returns the second arg when it's already absolute on Windows; on Linux pwsh, pass a relative temp name instead if this errors. If `pwsh` is unavailable, defer to the VM smoke test.

- [ ] **Step 3: Commit**

```bash
git add Initialize-Bifrost.ps1
git commit -m "feat(windows): .env rewrite + UTF-8-no-BOM writer"
```

---

### Task 4: Main flow — prompts, generate, write, launch, instructions

Wire the helpers into `Initialize-Bifrost`: overwrite guard, domain prompt, secret generation, `.env` assembly, `docker compose up -d`, and the final instructions. This replaces the `Write-Host 'Preflight OK...'; return 0` placeholder from Task 1 with the real body.

**Files:**
- Modify: `Initialize-Bifrost.ps1`

- [ ] **Step 1: Replace the preflight success placeholder with the full flow**

In `Initialize-Bifrost`, replace these two lines:
```powershell
    Write-Host 'Preflight OK (Docker running, .env.example present).'
    return 0
```
with:
```powershell
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
```

- [ ] **Step 2: Re-parse the full script**

Run (host pwsh if available, else defer to VM):
```bash
pwsh -NoProfile -Command '$null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path ./Initialize-Bifrost.ps1), [ref]$null, [ref]$null); "parse-ok"'
```
Expected: `parse-ok`.

- [ ] **Step 3: Dry-run `.env` generation only (no Docker)**

Run in a throwaway dir containing a copy of `.env.example` (host pwsh if available, else defer to VM):
```bash
tmp=$(mktemp -d); cp .env.example "$tmp/.env.example"; cp Initialize-Bifrost.ps1 "$tmp/"; cd "$tmp"; \
pwsh -NoProfile -Command './Initialize-Bifrost.ps1 -Domain app.example.com -Force -NoStart' ; \
echo '--- .env head ---'; head -c 3 .env | xxd | head -1; grep -E 'BIFROST_WEBAUTHN_ORIGIN|BIFROST_ENVIRONMENT|BIFROST_PUBLIC_URL|POSTGRES_PASSWORD' .env; cd - ; rm -rf "$tmp"
```
Expected: no BOM in the first 3 bytes (not `efbbbf`), `BIFROST_WEBAUTHN_ORIGIN=https://app.example.com`, `BIFROST_ENVIRONMENT=production`, `BIFROST_PUBLIC_URL=https://app.example.com`, and a long random `POSTGRES_PASSWORD`. If `pwsh` is unavailable on the host, defer this to the VM smoke test (Task 5).

- [ ] **Step 4: Commit**

```bash
git add Initialize-Bifrost.ps1
git commit -m "feat(windows): full Initialize-Bifrost flow (prompt, write, launch, instructions)"
```

---

### Task 5: VM smoke test (real launch on win11-pam)

End-to-end verification on the Windows VM per the spec. This is the authoritative verification for the feature.

**Files:** none (verification only).

- [ ] **Step 1: Copy the script to the VM checkout**

From the Linux host:
```bash
IP=192.168.122.175
sshpass -p 'YoureAbsolutelyRight!1' scp -o StrictHostKeyChecking=no \
  Initialize-Bifrost.ps1 \
  "developer@$IP:C:/Users/developer/bifrost-dev/bifrost/Initialize-Bifrost.ps1"
```
Expected: scp exit 0.

- [ ] **Step 2: Run it on the VM (localhost, interactive-equivalent)**

Use the base64-EncodedCommand pattern (see `/tmp/winps.py` from the RabbitMQ work) to run, from `C:\Users\developer\bifrost-dev\bifrost`:
```powershell
.\Initialize-Bifrost.ps1 -Domain localhost -Force
```
Note: the VM debug stack uses `docker-compose.debug.yml`, but this script targets the production `docker-compose.yml`. To avoid port/stack collisions with the running debug stack, first `cd` to a clean copy of the repo OR run with `-NoStart` and inspect `.env`, then optionally launch the production stack only if no debug stack is up. Decide at execution time based on what's running (`docker ps`).

- [ ] **Step 3: Verify `.env` correctness on the VM**

Confirm: `.env` exists, first 3 bytes are NOT `EF BB BF` (no BOM), `POSTGRES_PASSWORD`/`RABBITMQ_PASSWORD`/`SEAWEEDFS_SECRET_KEY`/`BIFROST_SECRET_KEY` are long random alphanumerics, `BIFROST_WEBAUTHN_RP_ID=localhost`, `BIFROST_WEBAUTHN_ORIGIN=http://localhost:3000`, `BIFROST_ENVIRONMENT=development`.

- [ ] **Step 4: Verify the stack serves (if launched)**

If the production stack was started, confirm `docker compose ps` shows services up and the access URL responds (e.g. `Invoke-WebRequest http://localhost:3000 -UseBasicParsing` returns a page, or the health endpoint returns 200). Capture the result in the smoke-test notes.

- [ ] **Step 5: Record the smoke-test outcome**

Append a short result block (pass/fail + evidence) to the spec file or a spot-check note under `docs/spot-checks/`. No code commit needed unless the script required fixes — if it did, fix in `Initialize-Bifrost.ps1`, re-copy, re-run, and commit the fix.

---

### Task 6: Delete `build.sh`

Parallel cleanup. Verified during planning: `build.sh` has zero references outside one historical `docs/` plan, and the `bifrost:release` skill is already CI-based and never mentions it. So this is a clean deletion.

**Files:**
- Delete: `build.sh`

- [ ] **Step 1: Confirm no live references remain**

Run:
```bash
rg -n "build\.sh" --glob '!docs/**' . | grep -vE "node_modules"
```
Expected: no output (the only matches are in `docs/`, which are historical plan records and stay).

- [ ] **Step 2: Delete the file**

```bash
git rm build.sh
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: remove unused build.sh (releases are CI-built, not manual)"
```

---

## Self-Review Notes

- **Spec coverage:** preflight guards (Task 1), secret generation + origin derivation (Task 2), `.env` rewrite + UTF-8-no-BOM (Task 3), prompts/launch/instructions + params `-Domain`/`-Force`/`-NoStart` (Task 4), VM smoke test (Task 5), `build.sh` deletion (Task 6). All spec sections map to a task.
- **Naming consistency:** helper names are stable across tasks — `New-BifrostSecret`, `Get-BifrostOrigin`, `Set-BifrostEnvLine`, `Write-BifrostEnvFile`, `Initialize-Bifrost`.
- **Host-vs-VM caveat:** `pwsh` may not be installed on the Linux host; every host-side verification step says to defer to the VM smoke test (Task 5) if so, so the plan never blocks on host tooling.
- **`.env` key contract** matches `setup.sh` and confirmed-present keys in `.env.example`.
