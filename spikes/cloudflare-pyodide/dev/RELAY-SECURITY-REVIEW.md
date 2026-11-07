# Relay review: SSRF scope and handler returns

CodeQL on 7d054cb reported partial SSRF and mixed explicit/fallthrough returns in
cloud-canonical-relay.py. The prefix-only route guard was insufficient: HTTP URL
normalization can move dot-segment paths outside the SDK namespace. Treat this as
a real path-scope defect, not a dismissed fixed-host false positive.

sdk_target now parses and decodes before authorization, rejects absolute/network
URLs, dot segments (including encoded/double-encoded forms), backslashes, control
characters and fragments, then constructs httpx.URL with literal scheme/host/port.
Only path/query are carried forward. Controller forwarding selects one of two
literal cloud URLs; it no longer concatenates a supplied profile URL and request
path. Redirect following and environment-proxy inheritance remain disabled.

Handler rejection paths now send a response and return without a value, consistent
with successful/fallthrough handling. No analyzer exclusions or alert dismissals.

CT128, existing pinned image, network none,0.5CPU/256MiB/64PIDs/30s:
pytest -q -o addopts= -o cache_dir=/tmp/pytest-cache --confcutdir=/qualification
/qualification/test_cloud_canonical_relay.py:13 pass in0.30s. Ruff passes.
Initial regression run had11 pass/2 fail: HTTPX adds a trailing question mark for
query=b''; passing None when no query preserves the exact valid request target.
Both original failures and the corrected run are retained in thread tool output.

Packet: /opt/bifrost-tests/pyodide-ddf316c1/inbox/relay-review-v1.
No cloud invocation/deployment, secret, endpoint reopening or production effect.
The earlier deployed acceptance remains evidence for its earlier relay source;
this change is tested locally in the guest only. Fresh hosted CodeQL remains
required before claiming its findings are cleared.