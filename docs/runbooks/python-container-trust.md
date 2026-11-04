# Python container trust

The API and development images preserve OpenSSL's default system trust selection.
The image build disables only the two retired distro anchors by SHA256 identity
in `/etc/ca-certificates.conf`, then rebuilds both the system bundle and hashed
certificate directory. Later `update-ca-certificates` runs preserve the exclusions.
Certificate and hostname verification remain enabled.

Existing private roots installed through `update-ca-certificates`, mounted system
stores, and explicit `SSL_CERT_FILE` / `SSL_CERT_DIR` selections keep their existing
behavior. No new bundle setting is required. HTTPX and SDK clients retain their
existing trust-selection precedence; updating certifi protects their default bundle.

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

Continue installing private CAs through the existing system certificate mechanism
or the installation's existing explicit trust selection. Never restore a retired
compromised root to make a connection pass. Rebuild/redeploy images to adopt future
CA and interpreter updates. Container settings do not modify the host store or
Chromium's separate trust implementation.

`tests/unit/core/test_python_trust_store.py` checks default contexts, pip's bundle,
and the hashed system directory for retired anchors. `test_prune_retired_ca.py`
checks persistent exclusions without deleting other roots. Production-image
verification also exercises a TLS connection using an installed synthetic private
CA, including after another system certificate-store update.

Run the private-CA control only in a disposable, credential-free container; it
changes that container's certificate store:

```bash
docker run --rm --network none --user 0 --entrypoint python \
  -v "$PWD/api/tests/image_checks/python_trust.py:/verify.py:ro" \
  -v "$PWD/api/tests/unit/core/test_python_trust_store.py:/trust_test.py:ro" \
  <production-candidate-image> -B /verify.py
```
