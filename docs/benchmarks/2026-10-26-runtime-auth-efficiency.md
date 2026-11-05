# Runtime authentication query pass

The baseline is `6da0464321b770158a3706bae2b59a283d3f52eb`, the deployed API
memory pass. A workflow whose acting user and run user are the same person
loaded that person's authorization context twice, then fetched their User row
twice while constructing the runtime principal. The second context was used
only to check existence.

The follow-up reuses those facts within one authentication pass. Every subsequent
request still reloads the durable run, current user authority and workflow
powers. Distinct actors retain their independent validation. There is no
cross-request authorization cache or TTL.

A Dockerized real-Postgres harness compared 20 warmed calls in fresh sessions
per variant, mounting the baseline modules for the control:

| Same-user workflow principal | Baseline | Follow-up |
|---|---:|---:|
| SQL statements per call | 15 | 10 |
| Median elapsed time | 8.620 ms | 5.172 ms |
| Total elapsed time, 20 calls | 174.621 ms | 104.767 ms |
| Total process CPU, 20 calls | 152.004 ms | 95.877 ms |

This is 33% fewer SQL statements and about 37% less process CPU in this control.
It does not establish an equivalent improvement to whole-workflow or production
HTTP latency. Those depend on dispatch, other requests, storage and external
services too.

The regression test failed at 15 statements before the repair and passes with a
10-statement budget afterwards. It also verifies that a subsequent call observes
admin-role revocation and user deletion. Existing runtime, caller and worker-SDK
consumer coverage passed 111 tests; API lint and types passed.
Integration coverage passed 16 tests, including scoped workflow SDK access,
real agent dispatch and summaries, and permission consent/revocation. Production
workflow/webhook measurements are retained separately in the operator's promotion
report, including the slower first-after sample and a settled warm sample.
