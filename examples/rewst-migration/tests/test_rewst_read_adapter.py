"""Tests the Solution's thin adapter over the bundled GraphQL transport."""

from __future__ import annotations

import json
import unittest

from modules.rewst_migration.rewst import RewstReadClient, RewstReadError


class RewstReadAdapterTests(unittest.TestCase):
    QUERY = "query Probe($id: ID!) { probe(id: $id) { id } }"

    def test_uses_cookie_transport_for_a_validated_read_query(self) -> None:
        observed: dict[str, object] = {}

        def transport(endpoint, headers, payload, timeout):
            observed.update(
                endpoint=endpoint,
                headers=headers,
                payload=json.loads(payload),
                timeout=timeout,
            )
            return 200, {}, b'{"data":{"probe":{"id":"safe"}}}'

        client = RewstReadClient(
            endpoint="https://api.rewst.io/graphql",
            session="private-cookie",
            transport=transport,
        )
        result = client.query(self.QUERY, "Probe", {"id": "safe"})
        self.assertEqual(result, {"data": {"probe": {"id": "safe"}}})
        self.assertEqual(observed["headers"]["Cookie"], "appSession=private-cookie")
        self.assertEqual(observed["payload"]["operationName"], "Probe")
        self.assertTrue(observed["payload"]["query"].lstrip().startswith("query"))

    def test_rejects_mutation_and_sanitizes_source_failures(self) -> None:
        client = RewstReadClient(
            endpoint="https://api.rewst.io/graphql",
            session="private-cookie",
            transport=lambda *_: (200, {}, b"{}"),
        )
        with self.assertRaisesRegex(RewstReadError, "compatibility"):
            client.query("mutation Destroy { destroy }", "Destroy", {})

        unauthorized = RewstReadClient(
            endpoint="https://api.rewst.io/graphql",
            session="private-cookie",
            transport=lambda *_: (401, {}, b"nope"),
        )
        with self.assertRaisesRegex(RewstReadError, "authorization") as error:
            unauthorized.query(self.QUERY, "Probe", {"id": "safe"})
        self.assertNotIn("private-cookie", str(error.exception))

        schema_error = RewstReadClient(
            endpoint="https://api.rewst.io/graphql",
            session="private-cookie",
            transport=lambda *_: (200, {}, b'{"errors":[{"message":"bad"}]}'),
        )
        with self.assertRaisesRegex(RewstReadError, "schema"):
            schema_error.query(self.QUERY, "Probe", {"id": "safe"})


if __name__ == "__main__":
    unittest.main()
