"""Distro exclusions must preserve administrator-added trust."""
import hashlib
import importlib.util
from pathlib import Path
import re
import ssl
import tempfile
import unittest
from unittest.mock import patch

import certifi


_spec = importlib.util.spec_from_file_location(
    "prune_retired_ca", Path(__file__).parents[3] / "scripts/prune_retired_ca.py",
)
assert _spec is not None and _spec.loader is not None
pruner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pruner)


class PruneRetiredCATests(unittest.TestCase):
    def test_bundle_removes_only_retired_roots_and_preserves_metadata(self):
        certificates = re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            Path(certifi.where()).read_text(),
            flags=re.DOTALL,
        )[:2]
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificates[0])).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text("# retained metadata\n" + "\n".join(certificates) + "\n")
            expected = "# retained metadata\n\n" + certificates[1] + "\n"
            with patch.object(pruner, "RETIRED_ANCHORS", {digest}):
                pruner.prune_retired_bundle(bundle)
                self.assertEqual(bundle.read_text(), expected)
                pruner.prune_retired_bundle(bundle)
                self.assertEqual(bundle.read_text(), expected)
            anchors = ssl.create_default_context(cafile=str(bundle)).get_ca_certs(binary_form=True)
            self.assertEqual(len(anchors), 1)
            self.assertNotIn(digest, {hashlib.sha256(cert).hexdigest() for cert in anchors})

    def test_preserves_other_roots_and_existing_exclusions(self):
        bundle = Path(certifi.where()).read_text()
        certificates = []
        for part in bundle.split("-----BEGIN CERTIFICATE-----")[1:3]:
            certificates.append("-----BEGIN CERTIFICATE-----" + part.split(
                "-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificates[0])).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "retired.crt").write_text(certificates[0])
            (root / "private.crt").write_text(certificates[1])
            config = root / "ca-certificates.conf"
            config.write_text("# administrator trust\nretired.crt\nprivate.crt\n!disabled.crt\n\n")
            expected = "# administrator trust\n!retired.crt\nprivate.crt\n!disabled.crt\n\n"
            with patch.object(pruner, "RETIRED_ANCHORS", {digest}):
                pruner.disable_retired_roots(config, root)
                self.assertEqual(config.read_text(), expected)
                pruner.disable_retired_roots(config, root)
                self.assertEqual(config.read_text(), expected)


if __name__ == "__main__":
    unittest.main()
