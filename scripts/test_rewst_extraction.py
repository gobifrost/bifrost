"""Offline checks for the Rewst GraphQL extraction helper.

These tests deliberately replace network transport and credential input.  They
must remain safe to run on a host with no Rewst access.
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).parents[1]
    / ".claude/skills/bifrost-rewst-migration/scripts/rewst_graphql_export.py"
)
SPEC = importlib.util.spec_from_file_location("rewst_graphql_export", SCRIPT)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


READ_QUERY = "query Workflows($limit: Int!, $offset: Int!) { workflows { id } }"


def manifest(requests: list[dict]) -> dict:
    return {"version": 1, "requests": requests}


class ManifestValidationTests(unittest.TestCase):
    def test_rejects_mutation_even_when_comment_and_string_contain_query(self) -> None:
        document = '# query disguised\nmutation Save($value: String!) { save(value: "query") { id } }'
        with self.assertRaisesRegex(exporter.ValidationError, "read-only"):
            exporter.validate_manifest(
                manifest(
                    [{"name": "save", "query": document, "result_path": "data.save"}]
                )
            )

    def test_accepts_query_when_comments_and_strings_name_mutation(self) -> None:
        document = '# mutation is not an operation\nquery Fetch { item(label: "mutation") { id } }'
        parsed = exporter.validate_manifest(
            manifest([{"name": "fetch", "query": document, "result_path": "data.item"}])
        )
        self.assertEqual(parsed.requests[0].name, "fetch")

    def test_rejects_shorthand_followed_by_mutation_and_bad_pagination_variable(
        self,
    ) -> None:
        with self.assertRaisesRegex(exporter.ValidationError, "read-only"):
            exporter.validate_manifest(
                manifest(
                    [
                        {
                            "name": "bad",
                            "query": "{ item { id } } mutation Change { save { id } }",
                            "result_path": "data.item",
                        }
                    ]
                )
            )
        with self.assertRaisesRegex(exporter.ValidationError, "declared"):
            exporter.validate_manifest(
                manifest(
                    [
                        {
                            "name": "bad-page",
                            "query": READ_QUERY,
                            "result_path": "data.workflows",
                            "pagination": {
                                "limit_variable": "limit",
                                "offset_variable": "wrong_offset",
                                "page_size": 10,
                            },
                        }
                    ]
                )
            )
        with self.assertRaises(exporter.ValidationError):
            exporter.validate_manifest(
                manifest(
                    [
                        {
                            "name": "summary",
                            "query": "query Fetch { item { id } }",
                            "result_path": "data.item",
                        }
                    ]
                )
            )

    def test_rejects_mutation_after_a_carriage_return_comment(self) -> None:
        query = "query Safe { item { id } } # comment\r mutation Write { save { id } }"
        with self.assertRaisesRegex(exporter.ValidationError, "read-only"):
            exporter.validate_manifest(
                manifest(
                    [
                        {
                            "name": "bad-comment",
                            "query": query,
                            "operation_name": "Write",
                            "result_path": "data.item",
                        }
                    ]
                )
            )

    def test_rejects_credentials_and_fragments_in_endpoint(self) -> None:
        for endpoint in (
            "http://example.test/graphql",
            "https://user:pass@example.test/graphql",
            "https://example.test/graphql#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(exporter.ValidationError):
                    exporter.validate_endpoint(endpoint)


class ExportTests(unittest.TestCase):
    def request(
        self, *, pagination: dict | None = None, operation_name: str | None = None
    ) -> object:
        item = {
            "name": "workflows",
            "query": READ_QUERY,
            "result_path": "data.workflows",
        }
        if pagination:
            item["pagination"] = pagination
        if operation_name:
            item["operation_name"] = operation_name
        return exporter.validate_manifest(manifest([item])).requests[0]

    def test_pagination_collects_exact_multiple_until_empty_page(self) -> None:
        calls: list[dict] = []

        def transport(
            _endpoint: str, _headers: dict, payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            calls.append(json.loads(payload))
            offset = calls[-1]["variables"]["offset"]
            page = (
                [{"id": str(offset + index)} for index in range(2)]
                if offset < 4
                else []
            )
            return 200, {}, json.dumps({"data": {"workflows": page}}).encode()

        records, pages = exporter.fetch_request(
            self.request(
                pagination={
                    "limit_variable": "limit",
                    "offset_variable": "offset",
                    "page_size": 2,
                }
            ),
            "https://example.test/graphql",
            "secret-token",
            transport,
        )
        self.assertEqual([record["id"] for record in records], ["0", "1", "2", "3"])
        self.assertEqual(pages, 3)
        self.assertEqual([call["variables"]["offset"] for call in calls], [0, 2, 4])

    def test_pagination_waits_for_empty_page_when_server_caps_page_size(self) -> None:
        items = [{"id": str(index)} for index in range(5)]
        offsets: list[int] = []

        def transport(
            _endpoint: str, _headers: dict, payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            offset = json.loads(payload)["variables"]["offset"]
            offsets.append(offset)
            return (
                200,
                {},
                json.dumps(
                    {"data": {"workflows": items[offset : offset + 2]}}
                ).encode(),
            )

        records, pages = exporter.fetch_request(
            self.request(
                pagination={
                    "limit_variable": "limit",
                    "offset_variable": "offset",
                    "page_size": 3,
                }
            ),
            "https://example.test/graphql",
            "secret-token",
            transport,
        )
        self.assertEqual(
            [record["id"] for record in records], ["0", "1", "2", "3", "4"]
        )
        self.assertEqual((pages, offsets), (4, [0, 2, 4, 5]))

    def test_pagination_rejects_repeated_page(self) -> None:
        def transport(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            return 200, {}, b'{"data":{"workflows":[{"id":"same"}]}}'

        with self.assertRaisesRegex(exporter.ExtractionError, "repeated page"):
            exporter.fetch_request(
                self.request(
                    pagination={
                        "limit_variable": "limit",
                        "offset_variable": "offset",
                        "page_size": 1,
                    }
                ),
                "https://example.test/graphql",
                "secret-token",
                transport,
            )

    def test_result_path_and_graphql_errors_are_sanitized(self) -> None:
        def broken_path(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            return 200, {}, b'{"data":{}}'

        with self.assertRaisesRegex(exporter.ExtractionError, "result_path"):
            exporter.fetch_request(
                self.request(),
                "https://example.test/graphql",
                "secret-token",
                broken_path,
            )

        def invalid_json(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            return 200, {}, b"not-json"

        with self.assertRaisesRegex(exporter.ExtractionError, "valid JSON"):
            exporter.fetch_request(
                self.request(),
                "https://example.test/graphql",
                "secret-token",
                invalid_json,
            )

        def graphql_error(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            return 200, {}, b'{"errors":[{"message":"secret-token must never appear"}]}'

        with self.assertRaises(exporter.ExtractionError) as raised:
            exporter.fetch_request(
                self.request(),
                "https://example.test/graphql",
                "secret-token",
                graphql_error,
            )
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertIn("GraphQL error", str(raised.exception))

        def null_result(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            return 200, {}, b'{"data":{"workflows":null}}'

        with self.assertRaisesRegex(exporter.ExtractionError, "null"):
            exporter.fetch_request(
                self.request(),
                "https://example.test/graphql",
                "secret-token",
                null_result,
            )

    def test_http_errors_retry_only_allowed_status_and_stay_sanitized(self) -> None:
        attempts = 0

        def transport(
            _endpoint: str, headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            nonlocal attempts
            attempts += 1
            self.assertEqual(headers["Cookie"], "appSession=secret-token")
            return 503, {"X-Secret": "secret-token"}, b"secret-token"

        with mock.patch.object(exporter.time, "sleep"):
            with self.assertRaises(exporter.ExtractionError) as raised:
                exporter.fetch_request(
                    self.request(),
                    "https://example.test/graphql",
                    "secret-token",
                    transport,
                    retries=1,
                )
        self.assertEqual(attempts, 2)
        self.assertNotIn("secret-token", str(raised.exception))
        self.assertEqual(str(raised.exception), "HTTP 503 after 2 attempts")

    def test_transport_timeout_is_sanitized(self) -> None:
        def transport(
            _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            raise TimeoutError("secret-token")

        with self.assertRaisesRegex(
            exporter.ExtractionError, "network request failed"
        ) as raised:
            exporter.fetch_request(
                self.request(),
                "https://example.test/graphql",
                "secret-token",
                transport,
            )
        self.assertNotIn("secret-token", str(raised.exception))

    def test_redirect_is_rejected_and_credential_never_enters_payload(self) -> None:
        observed: list[tuple[dict, bytes]] = []

        def transport(
            _endpoint: str, headers: dict, payload: bytes, _timeout: float
        ) -> tuple[int, dict, bytes]:
            observed.append((headers, payload))
            return 302, {"Location": "https://other.test"}, b""

        with self.assertRaisesRegex(exporter.ExtractionError, "redirect"):
            exporter.fetch_request(
                self.request(operation_name="Workflows"),
                "https://example.test/graphql",
                "secret-token",
                transport,
            )
        headers, payload = observed[0]
        self.assertEqual(
            headers,
            {
                "Cookie": "appSession=secret-token",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self.assertNotIn(b"secret-token", payload)
        self.assertEqual(json.loads(payload)["operationName"], "Workflows")


class CliOutputTests(unittest.TestCase):
    def test_hidden_prompt_fails_closed_when_getpass_warns(self) -> None:
        args = type("Args", (), {"token_env": None})()
        with mock.patch.object(
            exporter.getpass,
            "getpass",
            side_effect=exporter.getpass.GetPassWarning("no tty"),
        ):
            with self.assertRaisesRegex(
                exporter.ValidationError, "hidden appSession prompt"
            ):
                exporter._token_from_runtime(args, {})

    def test_cli_writes_private_files_and_refuses_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            manifest_path = temp / "requests.json"
            manifest_path.write_text(
                json.dumps(
                    manifest(
                        [
                            {
                                "name": "fetch",
                                "query": "query Fetch { item { id } }",
                                "result_path": "data.item",
                                "org_references": {"scope": "captured"},
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )
            output = temp / "output"

            def transport(
                _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
            ) -> tuple[int, dict, bytes]:
                return 200, {}, b'{"data":{"item":{"id":"one"}}}'

            stdout: list[str] = []
            with mock.patch.object(exporter, "default_transport", transport):
                result = exporter.main(
                    [
                        "--endpoint",
                        "https://example.test/graphql",
                        "--manifest",
                        str(manifest_path),
                        "--output",
                        str(output),
                        "--token-env",
                        "REWST_TEST_TOKEN",
                    ],
                    environ={"REWST_TEST_TOKEN": "secret-token"},
                    print_fn=stdout.append,
                )
            self.assertEqual(result, 0)
            self.assertFalse(
                any("one" in line or "secret-token" in line for line in stdout)
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            result_file = output / "fetch.json"
            self.assertEqual(stat.S_IMODE(result_file.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(result_file.read_text(encoding="utf-8"))["status"],
                "complete",
            )
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["coverage"], "requested_queries_only")
            self.assertEqual(
                summary["collections"][0]["org_references"], {"scope": "captured"}
            )

            with mock.patch.object(exporter, "default_transport", transport):
                self.assertEqual(
                    exporter.main(
                        [
                            "--endpoint",
                            "https://example.test/graphql",
                            "--manifest",
                            str(manifest_path),
                            "--output",
                            str(output),
                            "--token-env",
                            "REWST_TEST_TOKEN",
                        ],
                        environ={"REWST_TEST_TOKEN": "secret-token"},
                        print_fn=stdout.append,
                    ),
                    2,
                )
            link = temp / "symlink-output"
            link.symlink_to(output, target_is_directory=True)
            with self.assertRaises(exporter.ValidationError):
                exporter.create_private_output(link)

    def test_cli_marks_remaining_requests_unattempted_after_unauthorized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            manifest_path = temp / "requests.json"
            query = "query Fetch { item { id } }"
            manifest_path.write_text(
                json.dumps(
                    manifest(
                        [
                            {
                                "name": "first",
                                "query": query,
                                "result_path": "data.item",
                            },
                            {
                                "name": "second",
                                "query": query,
                                "result_path": "data.item",
                            },
                        ]
                    )
                ),
                encoding="utf-8",
            )
            calls = 0

            def transport(
                _endpoint: str, _headers: dict, _payload: bytes, _timeout: float
            ) -> tuple[int, dict, bytes]:
                nonlocal calls
                calls += 1
                return 401, {}, b"ignored"

            with mock.patch.object(exporter, "default_transport", transport):
                result = exporter.main(
                    [
                        "--endpoint",
                        "https://example.test/graphql",
                        "--manifest",
                        str(manifest_path),
                        "--output",
                        str(temp / "output"),
                        "--token-env",
                        "REWST_TEST_TOKEN",
                    ],
                    environ={"REWST_TEST_TOKEN": "secret-token"},
                    print_fn=lambda _line: None,
                )
            self.assertEqual(result, 1)
            self.assertEqual(calls, 1)
            self.assertIn(
                "not attempted",
                (temp / "output" / "second.json").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
