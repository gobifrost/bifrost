# HTTP transport option boundary

The exact bundled HTTPX fetch transport accepts unsupported options silently.
CT128 `ct128-sdk-probe.py --revision 8` exercised both real transport classes in
the pinned local Python Worker: 13 cases each, all accepted with empty instance
state. Cases include default construction, disabled verification, a nonexistent
CA path, a Python SSLContext, nonexistent client certificate/key paths, proxy,
Unix socket, local address, retries, HTTP version selection, pool limits and
socket options. No request method was called by these constructor tests.

The actual module source SHA256 is
`8710118e879ea03aad9782f7585426b7331f3933cbef465809416c4f4cfe9e48`,
matching the previously preserved complete transport source. Both constructors
contain only `pass`; `_do_fetch` forwards headers/body/method/signal. Thus the
empty state corroborates source inspection; constructor success is not evidence
that these options work. `transport_network_requests_attempted: 0` is a fixed
scope declaration, not a measured network counter. The provider additionally has
null global outbound and its container has network disabled.

The raw receipt records the exact probe file hashes, ABI, result and owned
container cleanup. Reproduce with a new staged packet/revision using the existing
`ct128-sdk-probe.py` harness, cached cf CLI and pinned bundle. Verify the receipt
with `node backend/verify-transport-options.mjs`. No new dependency, wheel,
compilation, ABI change, upstream patch or transport reimplementation was used.
This is an unsupported transport contract: compiling a certificate library does
not make the unchanged fetch call consume a Python SSLContext.

## SDK distinction and admission consequence

Upstream SDK139596497 passes `shared_ssl_context()` as `verify` to its regular
sync/async clients. `_tls.py` selects `SSL_CERT_FILE`, then `SSL_CERT_DIR`, then
certifi. Those Python trust-store choices are not forwarded by this transport.
Earlier genuine config/table/refresh proofs establish SDK HTTP functionality
through platform fetch, not equivalence to the native trust-store selection.
They do not prove that platform HTTPS validation is missing either.

A blanket constructor rejection of every SSLContext would also reject the SDK's
default path. Whitelisting a mutable shared object would not provide a security
boundary for arbitrary Python. No such bypass was added. Automatic pre-effect
refusal for dynamically selected transport requirements is **not implemented**;
arbitrary/untrusted admission remains disabled. Custom TLS/proxy/socket workloads
must stay on Linux under the current profile. This negative result is an explicit
acceptance gap, not an admission mechanism or a compatibility success.

Next useful implementation: establish a reviewed explicit workload capability
contract and fail unsupported declared requirements before dispatch, retaining
the upstream authoritative owner. A runtime adapter must separately qualify any
default-SDK exception and cannot claim it enforces arbitrary Python behavior.
No new cloud invocation/deployment or credential/security-setting change occurred.
