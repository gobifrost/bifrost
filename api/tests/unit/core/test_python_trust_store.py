"""Exercise the actual container OpenSSL store, including default CA inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
import ssl
import unittest

from pip._vendor import certifi as pip_certifi


RETIRED_ANCHORS = {
    # Public certificate DER SHA256 identities; no private keys/fixtures.
    # emSign ECC Root CA - G3 and the retired AAA cross-signing anchor.
    '86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b',
    'd7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4',
}


class PythonTrustStoreTests(unittest.TestCase):
    def test_default_store_rejects_retired_incident_anchors(self):
        context = ssl.create_default_context()
        anchors = {hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)}
        self.assertTrue(anchors)
        self.assertFalse(anchors.intersection(RETIRED_ANCHORS))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_pip_vendored_bundle_does_not_restore_retired_anchors(self):
        context = ssl.create_default_context(cafile=pip_certifi.where())
        anchors = {hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)}
        self.assertTrue(anchors)
        self.assertFalse(anchors.intersection(RETIRED_ANCHORS))

    def test_default_directory_cannot_restore_retired_anchors(self):
        paths = ssl.get_default_verify_paths()
        assert paths.capath is not None
        # OpenSSL loads hashed-directory roots lazily. Check each certificate,
        # rather than relying on get_ca_certs() before any TLS handshake.
        for certificate in Path(paths.capath).glob("*.pem"):
            with self.subTest(certificate=certificate.name):
                der = ssl.PEM_cert_to_DER_cert(certificate.read_text())
                self.assertNotIn(hashlib.sha256(der).hexdigest(), RETIRED_ANCHORS)


if __name__ == '__main__':
    unittest.main()
