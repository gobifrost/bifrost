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
  ForEach-Object { $_.Trim() } |
  Where-Object {
    $_ -and
    $_ -notmatch '^#' -and
    $_ -notmatch '^--hash=' -and
    $_ -match '=='
  } |
  ForEach-Object { ($_ -replace '\s+\\$','') } |
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

## Debian Container Findings

Debian stable packages often keep an older upstream version and backport
security fixes into the Debian package revision. Do not treat an upstream
version comparison as sufficient evidence that a Debian package is exploitable.

For Debian container findings:

1. Record the Snyk ID, CVE, package, installed version, introducing package,
   and runtime path.
2. Check the installed version and candidate version in the built image:

   ```sh
   apt-get update
   apt-cache policy <package>
   ```

3. Check Debian Security Tracker for the CVE and source package:
   - https://security-tracker.debian.org/tracker/<CVE>
   - https://security-tracker.debian.org/tracker/source-package/<source-package>
4. Classify the finding:
   - **Fix now** when Debian has a fixed candidate available in stable or
     stable-security.
   - **Track upstream/distro** when Debian still marks the stable package
     vulnerable and no fixed stable candidate exists.
   - **Accept with evidence** when Debian marks the issue ignored, postponed,
     no-DSA, or not applicable to our runtime path.
   - **Refactor later** when the finding only disappears by removing a runtime
     tool such as `awscli`, `git`, or `curl`.
5. Add Snyk ignores only for reviewed findings with a dated comment that names
   the Debian tracker status and the Bifrost follow-up decision. Do not add
   broad package-level ignores.

### Current API Image Snapshot

As of 2026-05-19, the API image is based on
`python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856`.
The container scan reports 8 high/critical Debian findings after the initial
base-image cleanup. The image already has the current Debian candidate versions.
Track the Snyk finding IDs in our backlog; use Debian Security Tracker for the
public CVE/source-package status during each review.

| Source package | Installed version | Snyk finding IDs | Debian status | Bifrost disposition |
| --- | --- | --- | --- | --- |
| `gnutls28` | `3.8.9-3+deb13u3` | `SNYK-DEBIAN13-GNUTLS28-16344302`, `SNYK-DEBIAN13-GNUTLS28-16344305`, `SNYK-DEBIAN13-GNUTLS28-16344314`, `SNYK-DEBIAN13-GNUTLS28-16344321`, `SNYK-DEBIAN13-GNUTLS28-16344352`, `SNYK-DEBIAN13-GNUTLS28-16344357` | Debian Security Tracker marks trixie vulnerable; fixed in sid/forky at `3.8.13-1`. | Track until Debian ships a trixie security fix or we can remove the `git`/`curl` runtime dependency chain. |
| `expat` | `2.7.1-2` | `SNYK-DEBIAN13-EXPAT-16650098` | Debian Security Tracker marks trixie vulnerable; fixed in sid at `2.8.0-2`. | Track until Debian ships a trixie security fix or we can remove the `git`/`awscli` runtime dependency chain. |
| `python-urllib3` | `2.3.0-3+deb13u1` | `SNYK-DEBIAN13-PYTHONURLLIB3-14193375` | Debian marks trixie vulnerable but ignored because the effective fix is intrusive and requires a newer `brotli`. | Accept as distro-reviewed residual for now; revisit if Debian publishes a stable fix or if `awscli` is removed. |

The remaining findings are not a reason to vendor newer Debian packages from
testing/unstable into the production image. Prefer either the supported Debian
stable security update, or a separate runtime-slimming change that removes the
introducing tool.

## Promotion Criteria

Make Snyk blocking only after at least one scheduled run and one representative
pull-request run have completed with acceptable noise. A reasonable first
blocking gate is high and critical open-source findings on production
dependencies only. Do not make Debian container findings blocking until the
triage process above has a low-noise suppress/track workflow.
