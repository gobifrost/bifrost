# Normal Image Admission Setup

`cloudflare_live_suite.py` is an opt-in wrapper around the existing black-box
suite. It does not alter generic conformance behavior or expose an API route.

The root-owned isolated API setup must provide all of these before the wrapper
runs:

- `BIFROST_CONFORMANCE_API_CONTAINER`: exact API container name, used only as
  an argv element to `docker exec -i`.
- `BIFROST_CONFORMANCE_SCOPE`: exactly the disposable suite's capacity scope.
- Normal external-capacity API configuration with the same dedicated capacity
  token as the disposable Worker. The bridge never receives or prints it.
- The pinned, normal Bifrost worker image and its canonical
  `python -m src.worker.main` entrypoint. A sleep/fake image does not qualify.
- No external capacity reconciler while the suite observes the intentional
  process stop; canonical exact-UID cleanup happens after that evidence.

For each first valid start, the wrapper records the reservation returned by the
unchanged suite, performs a fresh provider GET, and proceeds only when the
same worker UUID is still `prepared` with the exact UID and build. The bridge
then inserts one immutable external `ExecutionWorker` row. It does not update
or adopt existing rows. After provider start, it requires the same row to be
`ready`, bound to the same scope/UID/version, and to have both a launcher and
an API owner/session before the start is accepted.
