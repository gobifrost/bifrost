"""Regression coverage for management-service imports outside SDK routes."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[3]


def test_event_and_file_routers_defer_management_services_from_sdk_imports() -> None:
    """The SDK-selected handlers do not import management-only service graphs."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import json
                import sys

                from src.routers import events

                event_management_modules = {
                    "src.repositories.events",
                    "src.services.audit",
                    "src.services.cron_parser",
                    "src.services.events.registry",
                    "src.services.events.validation",
                    "src.services.solutions.guard",
                    "src.services.webhooks.registry",
                    "src.services.webhooks.auth",
                    "src.services.webhooks.lifecycle",
                    "src.services.webhooks.signing_secret",
                    "shared.scope_resolver",
                }
                event_loaded = sorted(event_management_modules.intersection(sys.modules))

                from src.routers import files

                print(json.dumps({
                    "event_loaded": event_loaded,
                    "file_storage_loaded": "src.services.file_storage" in sys.modules,
                    "event_handler": events.emit_topic_event.__name__,
                    "file_router": files.router.prefix,
                }))
                """
            ),
        ],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result = json.loads(completed.stdout)

    assert result["event_loaded"] == []
    assert result["file_storage_loaded"] is False
    assert result["event_handler"] == "emit_topic_event"
    assert result["file_router"] == "/api/files"
