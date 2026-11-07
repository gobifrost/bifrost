# Independent Terminal Delivery

The companion attempt journal now serializes terminal acknowledgment per route.
A stalled canonical callback no longer holds its global registry lock and blocks
unrelated dispatch or terminal delivery. Same-route duplicates still serialize;
the pre/post callback owner checks, explicit canonical acceptance, retained result,
and acknowledgment-gated retirement remain intact. The same lock applies to
authenticated retained-result reconciliation after collection loss.

This is preparation for automatic private-adapter terminal reporting. It does
not add another scheduler, durable queue, public result endpoint, or retry loop.
The private adapter still requires explicit reconciliation at this checkpoint.

## Evidence

Existing CT128 (`bifrost-pyodide-test-01`), 2 CPU/8 GiB, unchanged native checkout
`1d33150eb376b71f8a81b427920eda1b0447f829`. Docker image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Each ephemeral test container: network disabled, 0.5 CPU, 1 GiB, 64 PIDs,
40-second external wall bound. No laptop Docker or cloud calls.

Raw output, exact guest scripts, failed predecessor source and tested hashes are
in `../results/delivery-lock/`. Run the checked-in v2 script on the assigned guest
after staging its explicitly named packet; it refuses an existing packet/run
directory. Core commands inside the bounded container:

```sh
python -m unittest -v test_attempt_delivery
ruff check --no-cache attempt_journal.py test_attempt_delivery.py
pyright --project pyright-guest-fixtures.json attempt_journal.py test_attempt_delivery.py
```

- V1 old source: deterministic regression failure at unrelated dispatch while
  another route's ACK was held. Same test passed after the change.
- V1 quality attempt: Ruff could not create a cache in the read-only mount.
  Preserved; v2 uses its supported `--no-cache` option.
- V2: two regression tests pass; Ruff and Pyright pass (zero diagnostics).
  Owner replacement after the callback leaves the receipt unacknowledged and
  retirement blocked until explicit current-owner reconciliation succeeds.
- Six existing route-owner/reconnect tests pass against the changed journal.
  No full database/engine suite or deployed acceptance rerun for this local
  synchronization change. Prior real engine/SDK receipts remain historical.

Normal successful invocation completion does not require physical isolate
destruction. This increment proves receipt concurrency and fencing only; it
does not prove CPU termination, deployed isolation, or post-deadline cessation
of effects. Public admission remains disabled. Next: owned automatic terminal
delivery with surfaced failures, then actual canonical completion qualification.
