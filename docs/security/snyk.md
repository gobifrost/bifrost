# Snyk Rollout

Bifrost uses Snyk as an additional dependency, IaC, and container signal next
to GitHub-native security controls. It does not replace Dependabot, CodeQL,
secret scanning, or OpenSSF Scorecard.

## Local Setup

The local CLI is installed from npm:

```powershell
snyk --version
npm view snyk version
npm install --global snyk@<version>
```

Authenticate once per workstation and set the default organization:

```powershell
snyk auth
snyk config set org=<snyk-org-id-or-slug>
```

## Repository Setup

Add these repository settings before expecting the GitHub workflow to report
useful results:

- `SNYK_TOKEN` as a repository or organization secret.
- `SNYK_ORG` as a repository or organization variable when the default token
  organization is not the desired Bifrost organization.
- Import the GitHub repositories into Snyk so scheduled Snyk monitoring and
  dashboard triage work outside CI.

## Scan Surfaces

The initial workflow scans:

- Python dependencies from a temporary Snyk-compatible requirements file
  generated from `requirements.lock`.
- Client dependencies from `client/package-lock.json`.
- Kubernetes manifests under `k8s/`.
- Published `ghcr.io/mtg-thomas/bifrost-api:dev` and
  `ghcr.io/mtg-thomas/bifrost-client:dev` images on scheduled/manual runs.

The workflow is non-blocking during rollout. Treat it as tuning evidence until
the false-positive and duplicate-finding volume is understood.

For local open-source scans, use the lock-derived temporary Python manifest
and an isolated virtual environment:

```powershell
$tmpReq = Join-Path $env:TEMP 'bifrost-snyk-requirements.txt'
Get-Content requirements.lock |
  Where-Object { $_ -match '^[A-Za-z0-9_.-]+==' } |
  ForEach-Object { ($_ -split '\s+\\')[0] } |
  Set-Content -Encoding ascii $tmpReq

python -m venv .venv
.\.venv\Scripts\pip install --require-hashes -r requirements.lock

snyk test --file=$tmpReq --package-manager=pip --severity-threshold=high --skip-unresolved=true --command=.\.venv\Scripts\python.exe
snyk test --file=client/package-lock.json --package-manager=npm --severity-threshold=high
```

## Triage Policy

Start with this policy:

- Critical and high findings get reviewed first.
- Dependency findings that already have a Dependabot PR should be routed to
  that PR instead of creating duplicate work.
- Confirm whether a finding affects production dependencies before opening a
  security advisory or public issue.
- Container base-image findings need human review; do not auto-merge base image
  updates solely because Snyk reports a fix.
- License findings warn during rollout and should not block merges until the
  policy is explicitly tuned.

## Promotion Criteria

Make Snyk blocking only after at least one scheduled run and one representative
pull-request run have completed with acceptable noise. A reasonable first
blocking gate is high and critical open-source findings on production
dependencies only.
