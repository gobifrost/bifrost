# Runtime-owned workspace names

The preparer now refuses declared import roots and entry-module roots owned by
the upstream finder or this provider's bootstrap. In particular, a workspace
digest for `main`, `runtime_runner`, `runtime_guard`, `pyodide_refresh`, or
`sdk_cleanup` cannot establish the provenance of a module already supplied by
the provider. Refusal precedes authenticated source lookup and workflow effects.
This is a profile restriction, not a change to upstream's import semantics.

The restriction applies to the root namespace. A nested entry such as
`workflows/sdk_cleanup.py` remains valid. No claim is made that this list covers
every installed third-party module or that it provides an untrusted-code sandbox.

On CT128, the predecessor accepted all 17 new forbidden-name cases. The corrected
preparer passes all 10 test methods, including those cases and the nested-name
positive control; Ruff and Pyright also pass. The exact focused output is in
`../results/bootstrap-provenance/focused.txt`. These are native preparation tests
with an HTTP mock, separate from the real API/SDK control receipt in that folder.

Reproduce the focused check in the pinned qualification image with
`python -m unittest -v test_workspace_prepare`, followed by Ruff and Pyright as
recorded in the focused output. Reproduce the real control with the staged packet
and `ct128-canonical-failure.py --api-prepare --revision 93`; the harness owns
resource guards and stops only its test project/provider, preserving volumes.

No Cloudflare invocation, deployment, new access or runtime-setting change is
part of this increment. Deployed interruption, isolation and memory enforcement,
full native startup, and canonical pre-launch refusal retirement remain open.

The real API/workerd control passed in 72.332 seconds (whole harness lifecycle):
SDK config/table effects, duplicate dispatch, stale-owner fencing, lost-ACK replay,
credential revocation and accounting [2,2,1,0] remained intact. No owned test
containers remained after cleanup. This is local, not deployed Cloudflare proof.
