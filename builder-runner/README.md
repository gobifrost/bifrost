# Bifrost builder runner

Provider-neutral sandbox harness for external `PlatformJob` attempts.

The runner accepts exactly one JSON job envelope from a file or stdin and uses only the
job-bound callback API under:

`{callback_base_url}/api/internal/sandbox/jobs/{job_id}`

It never receives Cloudflare, Bifrost user, or AI provider credentials. The
only authorization material is the short-lived job capability from the
envelope.

## Contract

Envelope fields:

- `schema_version`: must be `1`
- `job_id`: UUID string
- `job_type`: `solution.build` or `solution.builder.turn`
- `dispatch_attempt`: positive integer
- `callback_base_url`: absolute HTTP(S) URL without credentials or fragment
- `capability`: bearer capability for the exact job attempt
- `input_sha256`: lowercase SHA-256 of the staged input archive
- `timeout_seconds`: positive integer capped by the harness

`solution.build`:

1. `GET /input`
2. validate the input digest
3. extract safely under `/work/input`
4. run the discovered build command
5. upload `dist/**` through `PUT /artifacts/{path}`
6. `POST /complete`

`solution.builder.turn`:

1. `GET /input`
2. `GET /context`
3. run a deterministic bounded LLM loop via `POST /llm/completions`
4. upload a workspace archive through `PUT /output`
5. `POST /complete`

The turn loop sends restored context, accepts assistant text and tool calls
from the callback proxy, and exposes the same eight root-scoped workspace
tools as the native Builder Agent. Agents with a bundle also receive the
bundle-root-scoped `read_skill_asset` tool.

## Docker

`Dockerfile` is derived from `docker.io/cloudflare/sandbox:0.12.5`, matching
Cloudflare Sandbox SDK guidance that the runtime image version must match the
SDK version. The Dockerfile intentionally does not override the Sandbox image
entrypoint. Use this command inside the sandbox runtime:

```bash
bifrost-sandbox-runner /work/bifrost-job.json
```

## Local/self-hosted control service

The image also exposes a long-running control service for local/self-hosted
deployment. It uses a small HTTP API on port `8300` protected by
`BIFROST_RUNNER_SECRET` and executes only one fixed harness command per job:
`/usr/local/bin/bifrost-sandbox-runner <job-file>`. No command strings are
constructed from request data.

Endpoint summary:

- `GET /health`
- `POST /provision`
- `POST /jobs` with body `{"instance_id", "job"}`
- `DELETE /jobs/{run_id}`

Run the control service container entrypoint with:

```bash
docker run --rm \
  -e BIFROST_RUNNER_SECRET=<secret> \
  -p 8300:8300 \
  <image>:latest
```

To run a one-shot harness as before, keep the existing command path:

```bash
docker run --rm <image>:latest \
  bifrost-sandbox-runner /work/bifrost-job.json
```
