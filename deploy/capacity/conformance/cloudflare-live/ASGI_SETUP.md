# Isolated API Entry for Normal-Image Conformance

Use this only in a disposable Compose project. It is not an edit to any
historical Cloudflare fixture or a production deployment option.

The isolated API service must replace its normal command and prepend this
directory to `PYTHONPATH` so the guarded entrypoint is imported before
`src.main`:

```yaml
services:
  api:
    environment:
      BIFROST_CONFORMANCE_SCOPE: <exact-disposable-capacity-scope>
      PYTHONPATH: /deploy/capacity/conformance/cloudflare-live:/app
    command:
      - uvicorn
      - conformance_asgi:app
      - --host
      - 0.0.0.0
      - --port
      - "8000"
      - --timeout-graceful-shutdown
      - "300"
```

Start that isolated project with no worker or scheduler replicas. The shim
requires a nonempty valid `BIFROST_CONFORMANCE_SCOPE`; it replaces only
`src.services.kubernetes_control.start_kubernetes_loops` before importing the
canonical application. API dispatch, WebSocket remote control, signed external
worker admission, and remote-owner execution continue through `src.main`.

The scope must equal the suite and bridge scope. The API's ordinary external
capacity URL/token configuration must still use the same dedicated token as the
disposable Worker. Do not use this entrypoint for any historical fixture.
