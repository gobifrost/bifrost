# Python container trust

The API and development images select the CA bundle from their hash-locked
certifi dependency for Python/OpenSSL default TLS contexts. `SSL_CERT_FILE`
points to the image's `/etc/bifrost-ca/cacert.pem`; `SSL_CERT_DIR` points to an
empty directory so a system CA directory cannot reintroduce retired roots.
This also covers the S3 transport's `ssl.create_default_context()` path;
updating certifi alone would leave that path using the operating system store.
Certificate and hostname verification remain enabled.

The dependency lock updates certifi to 2026.10.19 and AnyIO to 4.14.2 for
[GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) and
[GHSA-82r6-8w77-94w6](https://github.com/advisories/GHSA-82r6-8w77-94w6).
Only their two existing lock records change; wheel/source hashes are from the
official version metadata. No application transport or storage behavior changes.

Both image builders and the runtime use the pinned official Python 3.14.10
security release. This includes interpreter-level rejection of compromised
intermediates and patched pip; pip's separate vendored bundle cannot be fixed by
changing only the application bundle. See the
[Python security announcement](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576).
The multiarch base is pinned to
`sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966`;
the registry records Docker Library source
`01dcc3d31fcd879f9af36e097bd96328a457f4bd`, `3.14/slim-trixie`.

Deployments needing a private CA can explicitly select a reviewed replacement
bundle with `SSL_CERT_FILE`. Include the required public roots and private CA;
retain an empty `SSL_CERT_DIR` unless an additional reviewed directory is
intentional. Never restore a retired compromised root to make a connection pass.
Rebuild/redeploy images to adopt future locked CA updates. These settings do
not modify the host store or qualify Chromium's separate trust implementation.

`tests/unit/core/test_python_trust_store.py` checks the actual default context,
retired public anchor identities, explicit-file root set and empty fallback
directory. The synthetic HTTPS fixture bundle can add its own test root without
weakening those checks. Live service handshakes and any provider-specific
acceptance remain separate from these credential-free trust checks.
