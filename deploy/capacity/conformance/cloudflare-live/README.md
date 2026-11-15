# Cloudflare Live Conformance Harness

This directory is a disposable qualification harness, not a production
Cloudflare provider. `capacity.conformance.mjs` is the test-only stop seam;
`cloudflare_live.py` is its conformance hook.

`stop-process` requires all of the following environment variables:

- `CF_HOOK_ALLOW_MUTATIONS=1`
- `CF_CONFORMANCE_WORKER_URL`: the disposable Worker HTTPS origin
- `CF_CONFORMANCE_SCOPE`: the exact disposable capacity scope
- `CF_CONFORMANCE_CONTROL_TOKEN`: the separate test-route bearer secret

It sends only a scoped, exact worker UUID/resource UID stop request. It does
not call provider `DELETE`, release a provider slot, or receive credentials as
command-line arguments.

`observe` delegates to the established Cloudflare hook. `restart-provider`
also delegates, but only after verifying `CF_PROVIDER_SOURCE` names a dry-run
bundle containing **both** `capacity.mjs` and `capacity-core.mjs`, where
`capacity.mjs` contains the conformance control token and stop-route markers.
The existing restart hook uploads those two names only; supplying the normal
production source would silently remove the test stop seam after a restart and
is rejected.
