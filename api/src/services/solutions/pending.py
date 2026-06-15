"""Deploy-time guard: which captured-but-unpulled entities are absent from the
incoming manifest (and therefore block the deploy)."""

from __future__ import annotations


def unpulled_blockers(
    pending: list[tuple[str, str]],  # [(entity_type, entity_id), ...] from pending_captures
    manifest_ids: dict[str, set[str]],  # {entity_type: {id, ...}} present in the deploy body
) -> list[tuple[str, str]]:
    """Return pending entities NOT present in the incoming manifest.

    An entity that is pending (captured, not yet pulled) AND absent from the
    manifest must block the deploy — otherwise the full-replace reconcile would
    delete it. An entity absent with NO pending row is a genuine delete and is
    NOT returned here.
    """
    out: list[tuple[str, str]] = []
    for etype, eid in pending:
        if eid not in manifest_ids.get(etype, set()):
            out.append((etype, eid))
    return out
