"""Round-trip assertions. Reads field-class metadata; checks each field obeys the path policy.
Risk-3: canonical JSON for blobs, explicit secret handling, sentinel-leak scan.
Risk-2(pairing): strict pairing that skips scrubbed key-parts and fails on missing/dupes."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from bifrost.field_classes import FieldClass, field_class_of, match_keys

Policy = dict[FieldClass, str]  # action per class: keep | scrub | stamp | remap


def canonical(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def assert_field_roundtrip(
    model: type[BaseModel],
    field: str,
    before: dict[str, Any],
    after: dict[str, Any],
    policy: Policy,
    row: Any,
    *,
    remap: Any | None = None,
) -> None:
    """Assert that *field* obeys the path *policy* when comparing *before* to *after*.

    remap: optional callable old_ref_id -> expected_new_ref_id.  Used to verify that a
    reference to an IN-BUNDLE entity was remapped to the EXACT expected id (Codex round-3
    fix — presence-only is too weak; a wrong remap must fail).  remap() returns None for
    an out-of-bundle ref it can't predict → skip the exact check for that ref only.
    """
    cls = field_class_of(model, field, row)
    action = policy[cls]
    bval, aval = before.get(field), after.get(field)

    if action == "keep":
        assert canonical(aval) == canonical(bval), (
            f"{model.__name__}.{field} ({cls.value}) changed {bval!r} -> {aval!r}"
        )
    elif action == "scrub":
        assert aval in (None, [], {}, ""), (
            f"{model.__name__}.{field} ({cls.value}) leaked on scrub: {aval!r}"
        )
    elif action == "stamp":
        assert aval is not None, (
            f"{model.__name__}.{field} ({cls.value}) not stamped"
        )
    elif action == "remap":
        assert (aval is None) == (bval is None), (
            f"{model.__name__}.{field} ({cls.value}) presence changed on remap"
        )
        if remap is not None and bval is not None:
            # EXACT-id check for in-bundle refs. For a list, map each element; for a
            # scalar, map it directly.  remap() returning None means out-of-bundle →
            # skip the exact check for that element.
            if isinstance(bval, list):
                # bval is a list, so the after-side must be too — a scalar here would
                # make zip() iterate a string char-by-char and silently mis-compare.
                assert isinstance(aval, list), (
                    f"{model.__name__}.{field} expected list after remap, got {type(aval).__name__}"
                )
                expected = [remap(x) for x in bval]
                # Only check elements where remap gave us a concrete expectation.
                for exp_item, act_item in zip(expected, aval):
                    if exp_item is not None:
                        assert str(act_item) == str(exp_item), (
                            f"{model.__name__}.{field} refs mis-remapped: {aval!r} != {expected!r}"
                        )
            else:
                exp = remap(bval)
                if exp is not None:
                    assert str(aval) == str(exp), (
                        f"{model.__name__}.{field} ref mis-remapped: {aval!r} != {exp!r}"
                    )
    else:
        raise AssertionError(f"unknown action {action!r}")


def assert_no_secret_leak(text: str, sentinels: list[str]) -> None:
    """Assert no sentinel string appears in *text* (the serialized non-secret envelope)."""
    for s in sentinels:
        assert s not in text, f"secret sentinel {s!r} leaked into a non-secret envelope"


def model_field_names(model: type[BaseModel]) -> set[str]:
    """The set of keys a path may legitimately emit for *model*, BY ALIAS.

    A field with an alias (``ManifestTable.table_schema`` alias ``schema``) is
    emitted under its alias by the real serializers (``by_alias=True``), so the
    completeness check must accept the alias, not the python name.
    """
    names: set[str] = set()
    for fname, info in model.model_fields.items():
        names.add(fname)
        if info.alias:
            names.add(info.alias)
    return names


def assert_dict_keys_accounted(
    model: type[BaseModel],
    emitted: dict[str, Any],
    extra_keys: set[str],
) -> None:
    """Completeness oracle (the "single model" gap, plan Full-dict coverage §).

    The ``Manifest*`` models are NOT a complete description of what every path
    serializes — capture/generate emit transport keys the model never names
    (e.g. agent ``max_run_timeout``, form ``workflow_path``, app ``logo_b64``).
    A field-class-only harness is structurally BLIND to such keys, which is
    exactly where a Bug-C silent drop hides.

    This asserts that EVERY key the path actually emitted is either (a) a
    classified ``Manifest*`` field (by name or alias) or (b) a key declared in
    the path's ``EXTRA_FIELD_POLICY`` (``extra_keys``).  An UNACCOUNTED key is a
    hard failure — it makes the model/serializer divergence VISIBLE instead of
    silently uncovered.
    """
    known = model_field_names(model) | extra_keys
    unaccounted = sorted(set(emitted) - known)
    assert not unaccounted, (
        f"{model.__name__}: emitted keys not classified and not in EXTRA_FIELD_POLICY: "
        f"{unaccounted}.  Either they are real ManifestModel fields that should be tagged, "
        f"or transport extras that must be declared in EXTRA_FIELD_POLICY with a code citation."
    )


def _surviving_key(model: type[BaseModel], policy: Policy) -> tuple[str, ...]:
    """Match-key fields whose class the path does NOT scrub/stamp.

    Codex round-2 fix: organization_id is stamped on solution paths → excluded, so we
    match on the stable parts (e.g. name) rather than the environment-specific ones.
    """
    out = []
    for f in match_keys(model):
        cls = field_class_of(model, f)  # static class — predicate not needed for key fields
        if policy.get(cls) in ("scrub", "stamp"):
            continue
        out.append(f)
    return tuple(out)


def _index(rows: list[dict], keyfn: Any, model: type[BaseModel]) -> dict[Any, list[dict]]:
    idx: dict[Any, list[dict]] = {}
    for r in rows:
        idx.setdefault(keyfn(r), []).append(r)
    return idx


def _take(idx: dict, key: Any, model: type[BaseModel], before: dict) -> dict:
    """Return exactly one hit; raise if zero or more than one (strict pairing)."""
    hits = idx.get(key, [])
    assert len(hits) == 1, (
        f"{model.__name__}: expected exactly 1 match for key {key!r}, "
        f"got {len(hits)} (before id={before.get('id')!r})"
    )
    return hits[0]


def pair_rows(
    model: type[BaseModel],
    before_rows: list[dict],
    after_rows: list[dict],
    strategy: str,
    policy: Policy,
    expected_id: Any | None = None,
) -> list[tuple[dict, dict]]:
    """STRICT: returns one (before, after) pair per before-row; raises on missing or duplicate.

    strategy:
      'by_id'         — _repo / same-env: match on ``id``.
      'by_remap'      — solution install: ``expected_id(before)`` gives the post-install id.
      'by_match_key'  — natural-key match using only the SURVIVING key parts (those whose
                        class is NOT scrubbed/stamped by *policy*).
    """
    pairs: list[tuple[dict, dict]] = []

    if strategy == "by_id":
        idx = _index(after_rows, lambda r: r["id"], model)
        for b in before_rows:
            pairs.append((b, _take(idx, b["id"], model, b)))

    elif strategy == "by_remap":
        assert expected_id is not None, "by_remap strategy requires expected_id(before) -> id"
        idx = _index(after_rows, lambda r: r["id"], model)
        for b in before_rows:
            pairs.append((b, _take(idx, expected_id(b), model, b)))

    elif strategy == "by_match_key":
        keys = _surviving_key(model, policy)
        assert keys, f"{model.__name__}: no surviving match-key fields under this policy"
        kf = lambda r: tuple(r.get(f) for f in keys)  # noqa: E731
        idx = _index(after_rows, kf, model)
        for b in before_rows:
            pairs.append((b, _take(idx, kf(b), model, b)))

    else:
        raise AssertionError(f"unknown strategy {strategy!r}")

    return pairs
