"""SDK package import boundary regressions.

The SDK keeps facade classes and models eagerly importable
(`import bifrost.workflows` exposes the public class), while the `ai`
facade stays lazy because it pulls in openai/anthropic (~1,078 modules).
"""

import json
import subprocess
import sys


def _run(code: str) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/app",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_ai_import_stays_lazy_until_first_access():
    payload = _run(
        "import json, sys; import bifrost; "
        "print(json.dumps({"
        "'openai_loaded': 'openai' in sys.modules, "
        "'anthropic_loaded': 'anthropic' in sys.modules, "
        "'has_ai_attr': hasattr(bifrost, 'ai')}))"
    )

    assert payload["openai_loaded"] is False
    assert payload["anthropic_loaded"] is False
    assert payload["has_ai_attr"] is True


def test_ai_access_loads_the_facade_class():
    payload = _run(
        "import json; import bifrost; "
        "ai = bifrost.ai; "
        "print(json.dumps({"
        "'type': type(ai).__name__, 'openai_loaded': True}))"
    )

    assert payload["type"] == "type"


def test_public_exports_keep_identity_and_order():
    code = r'''
import json

import bifrost

from bifrost import config, workflows
from bifrost import Organization
import bifrost.workflows as workflow_export

print(json.dumps({
    "all_prefix": bifrost.__all__[:5],
    "workflows_identity": workflows is bifrost.workflows is workflow_export,
    "config_identity": config is bifrost.config,
    "workflows_type": type(workflows).__name__,
    "organization_type": type(Organization).__name__,
}))
'''
    payload = _run(code)
    assert payload["all_prefix"] == [
        "agents",
        "artifacts",
        "api",
        "ai",
        "config",
    ]
    assert payload["workflows_identity"] is True
    assert payload["config_identity"] is True
    assert payload["workflows_type"] == "type"
    assert payload["organization_type"] == "ModelMetaclass"


def test_direct_submodule_import_preserves_public_class_exports():
    code = r'''
import json

import bifrost
import bifrost.workflows as workflows_export
from bifrost import executions

print(json.dumps({
    "workflows_identity": workflows_export is bifrost.workflows,
    "executions_identity": executions is bifrost.executions,
    "workflows_type": type(bifrost.workflows).__name__,
    "executions_type": type(bifrost.executions).__name__,
}))
'''
    payload = _run(code)
    assert payload == {
        "workflows_identity": True,
        "executions_identity": True,
        "workflows_type": "type",
        "executions_type": "type",
    }
