# Declared packages against observed runtime inventory

The private manifest binding now requires both `requirements` (capabilities) and
`packages` (exact distribution-name/version mapping). Nonempty package mappings
require the already validated, immutable Pyodide runtime inventory. Missing or
mismatched distributions fail before plan storage and physical dispatch. Names
use the same case/punctuation normalization as the inventory; ambiguous aliases
are refused. Versions are exact metadata strings, not requirement specifiers or
a dependency resolver. Input is bounded to32 records and128-character identities.

The bound mapping is copied into the attempt plan/fingerprint. Caller mutation
cannot change it. An explicit empty mapping declares no additional distribution
requirements; it is not an automatic fallback for a missing inventory or an
omitted declaration. Package install/update operations remain refused. Nothing
downloads code or changes the runtime in response to a workflow declaration.

The actual local fixture now binds the inventory from its existing credential-free
warmup before claims, then declares httpx0.28.1, pydantic2.14.2 and
pydantic-core2.50.2 for both reviewed workflows. This adds no warmup invocation.
The native Redis cache registration remains an optional diagnostic; inventory
binding does not pretend the full native host has started or authorize admission.

## Evidence and limits

CT128 focused command: `python -m unittest -v test_private_http_pool
test_runtime_inventory`, followed by Ruff/Pyright for the five changed Python
files. All33 tests and quality checks pass. Tests cover missing/unobserved/version
mismatch, alias collisions, caller mutation, explicit declarations, ownership,
duplicate dispatch and retained-result/deadline behavior. Raw output and real
control receipt are under `../results/package-binding/`; verify using
`node backend/verify-package-binding.mjs`.

Matching package metadata is necessary but insufficient for compatibility. The
separately pinned source bundle, SDK overlay and ABI remain authoritative; real
engine/SDK execution tests provide the behavioral evidence for the tested subset.
This is neither automatic dependency closure discovery nor an untrusted Python
sandbox. A missing dependency needs classification and wheel/build/runtime
qualification before a new bundle can be accepted; this check does not label an
absent package fundamentally incompatible or replace it with a clone.

No new dependency, native build, package patch, ABI, cloud invocation/deployment,
credential, runtime setting or guest allocation change. Public admission remains
disabled. Canonical setup-refusal acknowledgement, full native startup and
deployed abnormal cancellation/isolation remain open.

Actual control: CT128 ct128-canonical-failure.py --sdk-refresh --revision 4
exited 0 in 92.312 seconds (whole harness lifecycle). Both declarations matched
the observed runtime; real config/table effects, refresh/redaction, duplicate
delivery, stale-owner rejection, lost-ACK replay, revocation and accounting
[2,2,1,0] passed. Owned test containers stopped; volumes retained.
