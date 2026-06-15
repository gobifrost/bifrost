"""Unit + gate tests for the SDK-example linter (lint_examples.py).

The linter catches the deterministic code-example drift classes the build-skill
validation loop kept rediscovering (A10/A11 + the SDK-example audit):
- subscript on a tables.* return model (DocumentData/DocumentList are
  attribute-access only),
- a call to a nonexistent SDK method,
- a @workflow function with a ctx parameter,
- a v2 SDK symbol imported from an internal app-sdk path instead of "bifrost".

The final test is the GATE: every shipped reference doc's code examples must be
clean. A stale snippet fails here, in CI, instead of in a live build run.
"""
from pathlib import Path
import sys

_API = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_API / "scripts/skill-truth"))
import lint_examples  # noqa: E402

_REFERENCES = _API.parent / ".claude/skills/bifrost-build/references"


def _lint(md: str, filename: str = "references/x.md"):
    return lint_examples.lint_examples(md, filename)


def test_flags_subscript_on_tables_model():
    md = '```python\ndoc = await tables.insert("t", {})\nx = doc["id"]\n```\n'
    findings = _lint(md)
    assert any("subscript" in f.message and "doc" in f.message for f in findings)


def test_attribute_access_on_tables_model_is_clean():
    md = (
        "```python\n"
        'doc = await tables.insert("t", {})\n'
        "cid = doc.id\n"
        'res = await tables.query("t")\n'
        "for d in res.documents:\n"
        "    print(d.data.get('name'))\n"
        "```\n"
    )
    assert _lint(md) == []


def test_flags_unknown_sdk_method():
    md = '```python\nawait tables.fetchAll("t")\n```\n'
    findings = _lint(md)
    assert any("tables.fetchAll" in f.message for f in findings)


def test_real_sdk_method_is_clean():
    # tables.delete and tables.delete_document both exist — must not flag.
    md = '```python\nawait tables.delete("t", id)\nawait tables.delete_document("t", id)\n```\n'
    assert _lint(md) == []


def test_flags_ctx_param_on_workflow():
    md = "```python\n@workflow\nasync def main(ctx):\n    return {}\n```\n"
    findings = _lint(md)
    assert any("ctx" in f.message for f in findings)


def test_workflow_with_input_params_is_clean():
    md = (
        "```python\n@workflow\nasync def add_task(title: str, priority: str = 'low') -> dict:\n"
        "    return {'title': title}\n```\n"
    )
    assert _lint(md) == []


def test_flags_v2_symbol_from_internal_import():
    md = '```tsx\nimport { tables, TableNotFoundError } from "@/lib/app-sdk/tables";\n```\n'
    findings = _lint(md)
    assert any("internal path" in f.message for f in findings)


def test_v2_symbol_from_bifrost_is_clean():
    md = '```tsx\nimport { tables, TableNotFoundError } from "bifrost";\n```\n'
    assert _lint(md) == []


def test_all_reference_examples_are_clean():
    """GATE: every shipped reference doc's code examples must pass the linter.

    If this fails, a code snippet in a reference doc would crash at runtime or
    fail to build when copied — fix the snippet (or the linter if it's a false
    positive), don't ship it.
    """
    paths = sorted(_REFERENCES.glob("*.md"))
    assert paths, f"no reference docs found under {_REFERENCES}"
    findings = lint_examples.lint_example_paths(paths)
    assert not findings, (
        "SDK-example drift in shipped reference docs:\n"
        + "\n".join(f"  {f.filename}: {f.message}" for f in findings)
    )
