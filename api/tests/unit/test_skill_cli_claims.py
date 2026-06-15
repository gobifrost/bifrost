"""Unit tests for the CLI-claims linter (lint_claims.py).

Tests that:
- Globally-banned commands (watch, sync, push, pull, git, export, import) are
  always flagged regardless of context.
- Live entity mutations (bifrost <entity> create|update|delete) are forbidden
  in solution context but allowed in repo context.
- Unknown flags on known commands are flagged.
"""
from pathlib import Path
import sys

_API = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_API / "scripts/skill-truth"))
import lint_claims  # noqa: E402


def test_flags_banned_global_commands():
    md = (
        "```bash solution\nbifrost agents create --name x\n```\n"
        "```bash\nbifrost watch\n```\n"
    )
    findings = lint_claims.lint_text(md, filename="references/solutions.md")
    msgs = " ".join(f.message for f in findings)
    assert "watch" in msgs                  # globally banned
    assert "agents create" in msgs          # banned in solution context


def test_allows_entity_mutation_in_repo_context():
    md = "```bash repo\nbifrost agents create --name x\n```\n"
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert all("agents create" not in f.message for f in findings)


def test_flags_unknown_flag():
    md = "```bash\nbifrost orgs list --bogus-flag\n```\n"
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert any("bogus-flag" in f.message for f in findings)


def test_flags_unknown_verb_on_known_group():
    md = "```bash\nbifrost solution bogus\n```\n"
    findings = lint_claims.lint_text(md, filename="references/solutions.md")
    assert any("unknown verb" in f.message for f in findings)


def test_line_continuation_does_not_false_positive():
    md = (
        "```bash repo\n"
        "bifrost agents create --name x \\\n"
        "  --description y\n"
        "```\n"
    )
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert findings == [], f"wrapped invocation should be clean, got {[f.message for f in findings]}"


def test_help_alias_not_flagged():
    md = "```bash\nbifrost help\n```\n"
    findings = lint_claims.lint_text(md, filename="references/repo.md")
    assert all("unknown command" not in f.message for f in findings)
