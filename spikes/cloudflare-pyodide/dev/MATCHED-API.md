# Matched API runtime

`ct128-matched-api.py` booted the existing isolated canonical test project with
repository `./test.sh stack up`, cached dependency image and bounded Compose.
Source `1d33150eb376b71f8a81b427920eda1b0447f829`, schema
`20261101_table_name_unique`, readiness 200 and unauthenticated workflows 401
were observed. Full lifecycle: 55.604 seconds. The owned project was stopped;
the old dev stack was untouched. Raw receipt: `matched-api-v1.json`.

This establishes a matched local API prerequisite, not execution through the
new guarded canonical entry. Existing canonical engine/SDK receipts remain
separate. No API endpoint, Cloudflare binding, tunnel or cloud credential changed.

Disk qualification initially failed at 7,992 MiB. Two bounded archival passes
retained 34 exact duplicate runtime copies from stopped providers with completed
receipts. Archive checksums and original pins are in `archive-qualified-v1.json`
and `archive-qualified-v2.json`. Each archive was decompressed and verified before
removing its loose duplicate. No volume or lock was removed. Historical packets
need restoration into a fresh reproduction packet; do not restart their old runs.

## Admission increment

The guarded SDK-only candidate now rejects explicitly declared unsupported,
duplicate or malformed requirements before creating an API capability or child.
Allowed declarations are json, async-workflow, pinned-workspace and bifrost-http-sdk.
Direct HTTPS requires its separate qualified policy path; this candidate refuses
that declaration. Existing fixed fixtures without a declaration retain their
strict path/manifest restrictions. This is compatibility checking, not code
discovery, scheduling authorization or proof of a sandbox. Public/untrusted
admission remains disabled. The historical v2 build verifier pins its original
entry source; a new build/runtime receipt is required for this changed entry.

## Next execution and cloud acceptance packet

1. Assemble the changed candidate into a fresh owned packet and build with cf CLI.
2. Use a local-only API service bridge to this matched test API. Execute actual
   upstream engine/SDK success and failure workflows with native run identity;
   assert workspace hashes, table readback, peer denial, redaction, one launch
   after duplicate/reconnect, terminal idempotency and retirement/accounting.
3. Keep shared remote-pool overlays at their documented pins. Do not edit the
   Containers owner's checkout or shared platform source to make this pass.
4. Before cloud apply, verify exact account, Worker, source/schema origin,
   current bounded invocation/cost/expiry envelope and cleanup controls. Retired
   canary receipts are not a fresh window. Use cf CLI for build/deploy/readback.
5. Test only reviewed fixtures; distinguish normal completion/disposal from
   abnormal cancellation. Measure deployed limits and independently observed
   late effects/accounting. A caught CPU exception or closed response is not
   physical-stop proof. Preserve failed/uncertain runs without redispatch.
6. Close the temporary endpoint and remove its test secret; verify readback.

The renewed full-send instruction authorizes continuing delivery. It does not
make missing runtime evidence true or permit bypassing account/vendor controls.
