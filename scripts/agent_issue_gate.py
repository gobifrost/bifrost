#!/usr/bin/env python3
"""Shadow-mode agent issue-filing gate (read-only side, v1).

An agent drafts a GitHub issue; this checker decides whether the draft is
ready to file, a possible/confirmed duplicate, in need of one concrete piece
of evidence, an idea/support request/wrong destination, or private-review
material. It never files, comments, or publishes anything.

Concept provenance (no code copied from any of these):
- Argo Workflows / Airflow / Grafana practice: an evidence checklist on bug
  reports (repro, expected/actual, environment).
- Karpenter practice: acceptance criteria are separate from priority; this
  gate checks acceptance-readiness only and leaves prioritization out.
- rakazo `.github/workflows/issue-triage.yml` (Apache-2.0,
  https://github.com/elie222/rakazo): read-only analyzer returning fixed-schema
  JSON, with a separate restricted publisher. v1 builds only the analyzer;
  there is no publisher, no issue-write permission, no webhook, and no change
  to the human issue forms in `.github/ISSUE_TEMPLATE/`.

All fetched text (draft body, candidate titles) is untrusted data: it is only
compared as strings, echoed truncated, and scanned for secret patterns before
echo. Matched secret values are never echoed back.

Usage (repo root):
    py scripts/agent_issue_gate.py --draft draft.json --candidates cands.json
    py scripts/agent_issue_gate.py --draft draft.json --search-error

Draft schema (JSON object): title, body, kind (bug|feature|other|unknown),
number, author — plus one agent-only field, `evidence` (where the evidence
came from):
    {"kinds": ["safe_repro"|"failing_test"|"redacted_incident"],
     "provenance": ["observed"|"reproduced"|"inferred"],
     "source": "free text, e.g. commit, test path, incident reference"}
Declared kinds count only when also visible in the body (show, don't claim);
declared provenance merges with detected provenance. Unknown values are
warned and ignored. A secret-bearing source routes to private_review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

TOOL_VERSION = 1

VERDICTS = (
    "ready_to_file",
    "possible_duplicate",
    "confirmed_duplicate",
    "needs_evidence",
    "idea_support_wrong_destination",
    "private_review",
)

# ---------------------------------------------------------------------------
# Untrusted-text handling
# ---------------------------------------------------------------------------

_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{8,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{8,}")),
    ("chat_token", re.compile(r"\bxox[abpr]-?[A-Za-z0-9-]{8,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|"
            r"refresh[_-]?token|auth[_-]?token|service[_-]?key)\b"
            r"\s*[:=]\s*['\"]?[A-Za-z0-9_.~/-]{8,}['\"]?"
        ),
    ),
    (
        "password_assignment",
        re.compile(r"(?i)\bpassword\s*(?:is\s*)?[:=]\s*\S+"),
    ),
    (
        "bearer_value",
        re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+\S+"),
    ),
    (
        "url_with_password",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:]+:[^/\s@]+@[^\s]+"),
    ),
)

_EMBARGO_MARKERS = (
    "do not publish",
    "do not disclose",
    "coordinated disclosure",
    "embargo",
    "confidential",
    "customer data",
)

# Instruction markers stand on their own; noun markers are skipped when the
# author negates them ("No customer data is involved").
_EMBARGO_NEGATABLE = (
    "coordinated disclosure",
    "embargo",
    "confidential",
    "customer data",
)

_EMBARGO_NEGATION = re.compile(r"(?i)\b(?:no|without|never|not\b.{0,20}\binvolv)")


def _find_embargo(text: str) -> bool:
    lowered = text.lower()
    for marker in _EMBARGO_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker, start)
            if index < 0:
                break
            if marker in _EMBARGO_NEGATABLE and _EMBARGO_NEGATION.search(
                lowered[max(0, index - 24) : index]
            ):
                start = index + len(marker)
                continue
            return True
    return False

_ECHO_LIMIT = 200


def _mask_bearer(match: re.Match[str]) -> str:
    value = match.group(0).split()[-1].strip("\"'`")
    if _BEARER_PLACEHOLDER.match(value):
        return match.group(0)
    return "[REDACTED]"


def _safe_echo(text: str, limit: int = _ECHO_LIMIT) -> tuple[str, int]:
    """Truncate untrusted text and mask secret-shaped values.

    Returns (echo, redaction_count). Never returns a matched secret value.
    Bearer placeholders (`...`, `<token>`, …) are left in place.
    """
    redactions = 0
    echo = text
    for _name, pattern in _SENSITIVE_PATTERNS:

        def _mask(match: re.Match[str]) -> str:
            if _name == "bearer_value":
                return _mask_bearer(match)
            return "[REDACTED]"

        echo, count = pattern.subn(_mask, echo)
        redactions += count
    if len(echo) > limit:
        echo = echo[:limit] + "..."
    return echo, redactions


_BEARER_PLACEHOLDER = re.compile(
    r"^(?:\.\.\.|…|<[^>]*>|\[[^\]]*\]|YOUR_[A-Z_]+|XXX+|TOKEN|xxx+|\*+)$",
    re.IGNORECASE,
)


def _find_sensitive(text: str) -> list[str]:
    found: list[str] = []
    for name, pattern in _SENSITIVE_PATTERNS:
        for match in pattern.finditer(text):
            if name == "bearer_value":
                value = match.group(0).split()[-1].strip("\"'`")
                if _BEARER_PLACEHOLDER.match(value):
                    continue
            found.append(name)
            break
    return found


# Agent-only draft field (Eddie brief): where the evidence came from.
# Declared kinds count only when also visible in the body — show, don't claim.
_EVIDENCE_KINDS = ("safe_repro", "failing_test", "redacted_incident")
_EVIDENCE_PROVENANCE = ("observed", "reproduced", "inferred")
_EVIDENCE_SOURCE_LIMIT = 160


# ---------------------------------------------------------------------------
# Normalization / matching helpers
# ---------------------------------------------------------------------------

_TITLE_PREFIXES = re.compile(
    r"^(?:\[[^\]]+\]|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]+)\s*"
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _normalize_title(title: str) -> str:
    text = title.strip().lower()
    while True:
        stripped = _TITLE_PREFIXES.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    return {token for token in _WORD.findall(text.lower()) if len(token) >= 3}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# ---------------------------------------------------------------------------
# Evidence heuristics
# ---------------------------------------------------------------------------

_BUG_HINTS = ("bug",)
_FEATURE_HINTS = ("feature", "enhancement", "rfc")

_BUG_TITLE_MARKERS = ("[bug]", "bug:")
_FEATURE_TITLE_MARKERS = ("[feature]", "[rfc]", "feature:", "rfc")

_REPRO_MARKERS = (
    "steps to reproduce",
    "step to reproduce",
    "reproduction",
    "repro:",
    "repro steps",
    "how to reproduce",
)
_TEST_MARKERS = (
    "failing test",
    "regression test",
    "synthetic test",
    "unit test",
    "acceptance / tests",
    "tdd",
    "cover with unit",
    "test file",
    "test case",
    "pytest",
    "tests/",
    "::test",
)
_INCIDENT_MARKERS = (
    "redacted",
    "no token values",
    "no secrets",
    "metadata-only",
    "metadata only",
    "control run",
    "production incident",
)
_EXPECTED_MARKERS = ("expected behavior", "expected:", "expected result")
_ACTUAL_MARKERS = (
    "actual behavior",
    "actual:",
    "actual result",
    "current behavior",
    "current behaviour",
    "what happens",
    "result:",
)
_FAILURE_VERBS = (
    "fails",
    "failed",
    "failure",
    "breaks",
    "broken",
    "hangs",
    "wrong",
    "missing",
    "never completes",
    "no license",
    "exits 0 but",
    "refuses",
    "cannot recover",
)

_OPERATOR_MARKERS = (
    "operator",
    "owner",
    "agent",
    "ci job",
    "ci runner",
    "runner",
    "workflow",
    "administrator",
    "admin",
    "developer",
    "deploy",
    "user",
    "customer",
    "team",
)
_CONSTRAINT_MARKERS = (
    "workaround",
    "today",
    "currently",
    "instead",
    "cannot",
    "can't",
    "no ",
    "painful",
    "blocked",
    "constraint",
    "limitation",
    "missing",
    "without",
)
_OUTCOME_MARKERS = (
    "acceptance",
    "proposed change",
    "proposal",
    "desired",
    "done looks like",
    "acceptance criteria",
    "should ",
    "add ",
)

_SUPPORT_MARKERS = (
    "how do i",
    "should i",
    "is there anyway",
    "is there any way",
    "anyway to",
    "help",
    "structuring",
    "am i doing",
)
_IDEA_MARKERS = (
    "anybody else",
    "any interest",
    "wouldn't it be nice",
    "would it be nice",
    "just an idea",
    "thought worth",
    "discord idea",
    "what if",
)
_RFC_MARKERS = ("rfc design discussion", "no implementation yet", "decisions needed")

# Section-level signals of a bug report even without the full form.
_STRUCTURAL_BUG_MARKERS = ("symptom", "root cause", "traceback", "stack trace")

_OBSERVED_MARKERS = (
    "observed",
    "evidence",
    "traceback",
    "control run",
    "reproduced on",
    "validated by",
    "version",
    "logs",
)
_INFERRED_MARKERS = (
    "likely",
    "possibly",
    "suspect",
    "i think",
    "probably",
    "unclear",
    "maybe",
    "猜",
)


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


_NEGATED_REPRO = re.compile(
    r"(?i)\bno\b.{0,40}\brepro|\bwithout\b.{0,40}\brepro|"
    r"\brepro\b.{0,40}\b(unknown|missing|none)\b"
)


def _without_negated_repro(body: str) -> str:
    """Drop lines where the author says there is no repro.

    Prevents "No repro steps are known" from counting as repro evidence.
    """
    kept = [line for line in body.splitlines() if not _NEGATED_REPRO.search(line)]
    return "\n".join(kept)


def _has_numbered_steps(text: str) -> bool:
    steps = re.findall(r"(?m)^\s*\d+[.)]\s+\S", text)
    return len(steps) >= 2


# ---------------------------------------------------------------------------
# Typed result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateRef:
    number: int
    url: str
    confidence: str  # "confirmed" | "possible"
    reason: str


@dataclass(frozen=True)
class GateResult:
    verdict: str
    subtype: str | None = None
    duplicate_of: DuplicateRef | None = None
    duplicates_searched: bool = False
    duplicates_unknown: bool = False
    question: str | None = None
    evidence_kinds: tuple[str, ...] = ()
    evidence_provenance: tuple[str, ...] = ()
    evidence_source: str | None = None
    feature_operator: bool = False
    feature_constraint: bool = False
    feature_outcome: bool = False
    reasons: tuple[str, ...] = ()
    redactions: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["evidence_kinds"] = list(self.evidence_kinds)
        value["evidence_provenance"] = list(self.evidence_provenance)
        value["reasons"] = list(self.reasons)
        value["warnings"] = list(self.warnings)
        return value


@dataclass
class _Draft:
    title: str
    body: str
    kind_hint: str = "unknown"
    number: int | None = None
    author: str | None = None


@dataclass
class _Candidate:
    number: int
    title: str
    state: str = "unknown"
    kind: str = "issue"  # "issue" | "pr"
    author: str | None = None


# ---------------------------------------------------------------------------
# Core evaluation (pure, deterministic, no network)
# ---------------------------------------------------------------------------


def _classify_kind(draft: _Draft) -> str:
    hint = draft.kind_hint.strip().lower()
    title = draft.title.lower()
    body = draft.body.lower()
    if hint in _BUG_HINTS or _contains_any(title, _BUG_TITLE_MARKERS):
        return "bug"
    if hint in _FEATURE_HINTS or _contains_any(title, _FEATURE_TITLE_MARKERS):
        return "feature"
    # Feature structure wins over bug heuristics: a proposal that quotes
    # current-vs-expected behavior is still a feature request, not a bug.
    if _contains_any(
        body, ("## problem", "## proposed", "## acceptance", "acceptance criteria")
    ):
        return "feature"
    has_repro = _contains_any(body, _REPRO_MARKERS) or _has_numbered_steps(draft.body)
    has_expected = _contains_any(body, _EXPECTED_MARKERS)
    has_actual = _contains_any(body, _ACTUAL_MARKERS)
    if (has_repro and (has_expected or has_actual)) or (
        has_expected and has_actual
    ):
        return "bug"
    structural = sum(1 for marker in _STRUCTURAL_BUG_MARKERS if marker in body)
    if structural >= 2:
        return "bug"
    if _contains_any(body, _RFC_MARKERS):
        return "feature"
    return "other"


def _bug_evidence(body: str) -> tuple[list[str], list[str]]:
    searchable = _without_negated_repro(body)
    lowered = searchable.lower()
    kinds: list[str] = []
    if _contains_any(lowered, _REPRO_MARKERS) or _has_numbered_steps(body):
        kinds.append("safe_repro")
    if _contains_any(lowered, _TEST_MARKERS):
        kinds.append("failing_test")
    if _contains_any(lowered, _INCIDENT_MARKERS):
        kinds.append("redacted_incident")

    provenance: list[str] = []
    if "safe_repro" in kinds or "failing_test" in kinds:
        provenance.append("reproduced")
    if _contains_any(lowered, _OBSERVED_MARKERS):
        provenance.append("observed")
    elif not provenance and kinds:
        provenance.append("observed")
    if _contains_any(lowered, _INFERRED_MARKERS) and not kinds:
        provenance.append("inferred")
    return kinds, provenance


def _bug_expectation(body: str) -> tuple[bool, bool]:
    lowered = body.lower()
    has_expected = _contains_any(lowered, _EXPECTED_MARKERS)
    has_actual = _contains_any(lowered, _ACTUAL_MARKERS) or (
        _contains_any(lowered, _FAILURE_VERBS)
        and _contains_any(lowered, ("summary", "what happens", "actual", "result"))
    )
    return has_expected, has_actual


def _feature_slots(body: str) -> tuple[bool, bool, bool]:
    lowered = body.lower()
    return (
        _contains_any(lowered, _OPERATOR_MARKERS),
        _contains_any(lowered, _CONSTRAINT_MARKERS),
        _contains_any(lowered, _OUTCOME_MARKERS),
    )


def _idea_subtype(draft: _Draft) -> str:
    lowered = (draft.title + "\n" + draft.body).lower()
    title_lower = draft.title.lower()
    if _contains_any(lowered, _SUPPORT_MARKERS) and "?" in draft.body:
        return "support"
    if title_lower.startswith("[chore]") or title_lower.startswith("chore:"):
        return "wrong_destination"
    return "idea"


_CONTRAST_PAIRS: tuple[tuple[str, str], ...] = (
    ("inbound", "outbound"),
    ("add", "remove"),
    ("enable", "disable"),
    ("create", "delete"),
    ("register", "deregister"),
    ("import", "export"),
    ("login", "logout"),
    ("allow", "deny"),
)


def _has_contrast(left: set[str], right: set[str]) -> bool:
    only_left = left - right
    only_right = right - left
    return any(
        (first in only_left and second in only_right)
        or (second in only_left and first in only_right)
        for first, second in _CONTRAST_PAIRS
    )


def _match_duplicates(
    draft: _Draft, candidates: list[_Candidate], repo: str
) -> DuplicateRef | None:
    norm_title = _normalize_title(draft.title)
    title_tokens = _tokens(norm_title)
    best: DuplicateRef | None = None
    best_overlap = 0.0

    for cand in candidates:
        if draft.number is not None and cand.number == draft.number:
            continue
        cand_norm = _normalize_title(cand.title)
        if not cand_norm or not norm_title:
            continue
        # Confirmed: same normalized title (non-trivial), never the draft itself.
        if cand_norm == norm_title and len(norm_title) >= 20:
            return DuplicateRef(
                number=cand.number,
                url=f"https://github.com/{repo}/issues/{cand.number}",
                confidence="confirmed",
                reason=(
                    f"identical normalized title to {cand.kind} "
                    f"#{cand.number} ({cand.state})"
                ),
            )
        # Possible: strong token overlap on a distinctive title. Never
        # confirmed: near-identical titles can be opposite proposals from
        # the same author (e.g. "keyless inbound auth" vs "keyless outbound
        # auth"), so only an identical normalized title confirms.
        overlap = _jaccard(title_tokens, _tokens(cand_norm))
        shared = title_tokens & _tokens(cand_norm)
        if overlap >= 0.5 and len(shared) >= 4 and len(norm_title) >= 20:
            contrast = _has_contrast(title_tokens, _tokens(cand_norm))
            reason = (
                f"{len(shared)} shared title terms "
                f"(overlap {overlap:.2f}) with {cand.kind} "
                f"#{cand.number} ({cand.state})"
            )
            if contrast:
                reason += "; titles differ by a contrast pair, human must decide"
            if (
                draft.author
                and cand.author
                and draft.author.lower() == cand.author.lower()
            ):
                reason += "; same author"
            candidate_ref = DuplicateRef(
                number=cand.number,
                url=f"https://github.com/{repo}/issues/{cand.number}",
                confidence="possible",
                reason=reason,
            )
            if best is None or overlap > best_overlap:
                best = candidate_ref
                best_overlap = overlap
    return best


def evaluate_draft(
    draft_dict: dict,
    candidates: list[dict] | None,
    *,
    repo: str = "gobifrost/bifrost",
    search_error: bool = False,
) -> GateResult:
    """Evaluate one agent draft. Pure function: no network, no side effects."""
    reasons: list[str] = []
    warnings: list[str] = []
    redactions = 0

    title = str(draft_dict.get("title", "") or "")
    body = str(draft_dict.get("body", "") or "")
    draft = _Draft(
        title=title,
        body=body,
        kind_hint=str(draft_dict.get("kind", "unknown") or "unknown"),
        number=draft_dict.get("number"),
        author=draft_dict.get("author"),
    )

    # Agent-only evidence declaration ("where the evidence came from").
    # Strict rule: declared kinds count only when also visible in the body.
    declared_kinds: list[str] = []
    declared_provenance: list[str] = []
    evidence_source_raw: str | None = None
    evidence_decl = draft_dict.get("evidence")
    if evidence_decl is not None:
        if not isinstance(evidence_decl, dict):
            warnings.append("draft 'evidence' must be an object; ignored")
        else:
            raw_kinds = evidence_decl.get("kinds", [])
            raw_prov = evidence_decl.get("provenance", [])
            if not isinstance(raw_kinds, list) or not isinstance(raw_prov, list):
                warnings.append("draft 'evidence.kinds/provenance' must be lists")
            else:
                declared_kinds = [k for k in raw_kinds if k in _EVIDENCE_KINDS]
                declared_provenance = [
                    p for p in raw_prov if p in _EVIDENCE_PROVENANCE
                ]
                unknown = [k for k in raw_kinds if k not in _EVIDENCE_KINDS] + [
                    p for p in raw_prov if p not in _EVIDENCE_PROVENANCE
                ]
                if unknown:
                    warnings.append(
                        f"unknown evidence values ignored: {', '.join(map(str, unknown))}"
                    )
            raw_source = evidence_decl.get("source")
            if raw_source is not None:
                evidence_source_raw = str(raw_source)

    combined = f"{title}\n{body}\n{evidence_source_raw or ''}"
    sensitive = _find_sensitive(combined)
    if sensitive:
        _echo, count = _safe_echo(combined)
        redactions += count
        return GateResult(
            verdict="private_review",
            duplicates_searched=candidates is not None,
            duplicates_unknown=search_error,
            evidence_kinds=(),
            evidence_provenance=(),
            reasons=(
                f"draft matches sensitive-data patterns: "
                f"{', '.join(sorted(set(sensitive)))}; "
                f"route to private review without publishing",
            ),
            redactions=redactions,
            warnings=tuple(warnings),
        )
    if _find_embargo(combined):
        return GateResult(
            verdict="private_review",
            duplicates_searched=candidates is not None,
            duplicates_unknown=search_error,
            reasons=(
                "draft asks for non-public handling; "
                "route to private review without publishing",
            ),
            redactions=redactions,
            warnings=tuple(warnings),
        )

    # Duplicates: unknown when the search failed, never "no duplicate".
    # The evidence source echo is safe here: secret-bearing sources already
    # returned private_review above via the combined scan.
    evidence_source: str | None = None
    if evidence_source_raw is not None:
        evidence_source, _count = _safe_echo(
            evidence_source_raw, _EVIDENCE_SOURCE_LIMIT
        )
        redactions += _count
    duplicates_searched = candidates is not None
    duplicates_unknown = search_error
    if search_error:
        warnings.append("duplicate search failed; duplicates unknown, not absent")
    if candidates is not None:
        parsed = [
            _Candidate(
                number=int(item.get("number", -1)),
                title=str(item.get("title", "") or ""),
                state=str(item.get("state", "unknown") or "unknown"),
                kind=str(item.get("kind", "issue") or "issue"),
                author=item.get("author"),
            )
            for item in candidates
            if isinstance(item, dict) and int(item.get("number", -1)) >= 0
        ]
        match = _match_duplicates(draft, parsed, repo)
        if match is not None:
            verdict = (
                "confirmed_duplicate"
                if match.confidence == "confirmed"
                else "possible_duplicate"
            )
            return GateResult(
                verdict=verdict,
                duplicate_of=match,
                duplicates_searched=True,
                duplicates_unknown=duplicates_unknown,
                reasons=(match.reason,),
                redactions=redactions,
                warnings=tuple(warnings),
            )
        reasons.append(
            f"no duplicate among {len(parsed)} searched issues/PRs"
            + ("; search had earlier failed, so this is partial" if search_error else "")
        )
    else:
        reasons.append("duplicate search not provided; duplicates unknown, not absent")

    kind = _classify_kind(draft)

    if kind == "bug":
        kinds, provenance = _bug_evidence(body)
        has_expected, has_actual = _bug_expectation(body)
        # Declared evidence must also be visible in the body: show, don't claim.
        if declared_kinds:
            invisible = [k for k in declared_kinds if k not in kinds]
            if invisible:
                reasons.append(
                    f"agent-declared evidence not visible in draft body: "
                    f"{', '.join(invisible)}; include the repro, test, or "
                    f"incident evidence in the draft"
                )
            for item in declared_provenance:
                if item not in provenance:
                    provenance.append(item)
        if evidence_source is not None:
            reasons.append(f"agent-declared evidence source: {evidence_source}")
        if kinds and has_expected and has_actual:
            reasons.append(
                f"bug evidence: {', '.join(kinds)} "
                f"(provenance: {', '.join(provenance) or 'unstated'}); "
                f"expected and actual behavior both stated"
            )
            return GateResult(
                verdict="ready_to_file",
                duplicates_searched=duplicates_searched,
                duplicates_unknown=duplicates_unknown,
                evidence_kinds=tuple(kinds),
                evidence_provenance=tuple(provenance),
                evidence_source=evidence_source,
                reasons=tuple(reasons),
                redactions=redactions,
                warnings=tuple(warnings),
            )
        if not kinds:
            question = (
                "What are the shortest reliable steps to trigger this "
                "(or the failing test / redacted incident evidence), and on "
                "which commit or version did you see it?"
            )
        elif not has_expected:
            question = (
                "What should happen instead -- what is the expected behavior "
                "for this report?"
            )
        else:
            question = (
                "What actually happens today -- what is the observed behavior "
                "versus the expected one?"
            )
        return GateResult(
            verdict="needs_evidence",
            duplicates_searched=duplicates_searched,
            duplicates_unknown=duplicates_unknown,
            question=question,
            evidence_kinds=tuple(kinds),
            evidence_provenance=tuple(provenance),
            evidence_source=evidence_source,
            reasons=tuple(reasons + ["bug draft is missing required evidence"]),
            redactions=redactions,
            warnings=tuple(warnings),
        )

    if kind == "feature":
        has_operator, has_constraint, has_outcome = _feature_slots(body)
        if has_operator and has_constraint and has_outcome:
            reasons.append(
                "feature states a concrete operator/workflow, a "
                "workaround-or-constraint, and a desired outcome"
            )
            return GateResult(
                verdict="ready_to_file",
                duplicates_searched=duplicates_searched,
                duplicates_unknown=duplicates_unknown,
                feature_operator=has_operator,
                feature_constraint=has_constraint,
                feature_outcome=has_outcome,
                reasons=tuple(reasons),
                redactions=redactions,
                warnings=tuple(warnings),
            )
        if not has_operator:
            question = (
                "Which concrete operator or workflow needs this, and what do "
                "they do today instead?"
            )
        elif not has_constraint:
            question = (
                "What is painful or blocked today -- the workaround or "
                "constraint this removes?"
            )
        else:
            question = (
                "What observable outcome would show this is done "
                "(acceptance criteria)?"
            )
        return GateResult(
            verdict="needs_evidence",
            duplicates_searched=duplicates_searched,
            duplicates_unknown=duplicates_unknown,
            question=question,
            feature_operator=has_operator,
            feature_constraint=has_constraint,
            feature_outcome=has_outcome,
            reasons=tuple(reasons + ["feature draft is missing required slots"]),
            redactions=redactions,
            warnings=tuple(warnings),
        )

    subtype = _idea_subtype(draft)
    reasons.append(f"draft reads as {subtype}, not a bug/feature report")
    return GateResult(
        verdict="idea_support_wrong_destination",
        subtype=subtype,
        duplicates_searched=duplicates_searched,
        duplicates_unknown=duplicates_unknown,
        reasons=tuple(reasons),
        redactions=redactions,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# CLI (read-only: files in, JSON out; never files/publishes anything)
# ---------------------------------------------------------------------------


def _load_json_file(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: file not found: {path}")
    except json.JSONDecodeError as error:
        raise SystemExit(f"error: invalid JSON in {path}: {error.msg}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-mode agent issue-filing gate (read-only; v1 analyzer only)."
    )
    parser.add_argument("--draft", required=True, help="JSON draft file")
    parser.add_argument(
        "--candidates",
        default=None,
        help="JSON file: list of {number,title,state,kind,author} to search",
    )
    parser.add_argument(
        "--search-error",
        action="store_true",
        help="the live duplicate search failed; duplicates are unknown",
    )
    parser.add_argument(
        "--repo",
        default="gobifrost/bifrost",
        help="owner/repo used to build duplicate URLs",
    )
    parser.add_argument(
        "--self-number",
        type=int,
        default=None,
        help="draft issue number to exclude from duplicate matching",
    )
    args = parser.parse_args(argv)

    if args.candidates is None and not args.search_error:
        print(
            "error: pass --candidates <file> or --search-error; "
            "duplicates must be searched or explicitly marked unknown",
            file=sys.stderr,
        )
        return 2

    draft_value = _load_json_file(Path(args.draft))
    if not isinstance(draft_value, dict):
        print("error: draft file must contain a JSON object", file=sys.stderr)
        return 2
    if args.self_number is not None and "number" not in draft_value:
        draft_value = dict(draft_value, number=args.self_number)

    candidates: list[dict] | None = None
    if args.candidates is not None:
        candidates_value = _load_json_file(Path(args.candidates))
        if not isinstance(candidates_value, list):
            print("error: candidates file must contain a JSON list", file=sys.stderr)
            return 2
        candidates = [item for item in candidates_value if isinstance(item, dict)]

    result = evaluate_draft(
        draft_value,
        candidates,
        repo=args.repo,
        search_error=args.search_error,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
