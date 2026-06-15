# Overnight desloppify report — triggers code

**Date:** 2026-06-14 (autonomous overnight run)
**Scope:** the night's NEW code only (the triggers feature: migration, ORM, capture/export/
install wiring, `_upsert_events` deploy, events-router guards, tests). Per the agreed policy:
auto-fix safe/mechanical findings, report big-judgment ones, change nothing risky unreviewed.

## Result: clean. No safe fixes needed; no risky findings introduced by this work.

Detectors run (`/tmp/desloppify-venv/bin/desloppify`):

| Detector | Target | Finding |
|----------|--------|---------|
| `unused` (symbols) | `deploy.py` | None |
| `unused --category imports` | `capture.py`, `export.py`, `zip_install.py` | None |
| `dupes` | `api/src/services/solutions` | 2 pairs — **both pre-existing, NOT this work** (see below) |
| `large` | `deploy.py` | File-level LOC only; pre-existing (see below) |

### Pre-existing findings (NOT introduced tonight — left for a separate pass)
1. **Dupe: `__init__`** — `capture.py:106` ≡ `dependency_walker.py:112` (100%). Two service classes
   with identical constructors (`db`, `repo`). Pre-existing; a shared base or mixin would dedupe but
   it's a judgment refactor across two services — out of scope for the triggers run.
2. **Dupe: `_read_readme`** — `git_sync.py:139` ≡ `zip_install.py:203` (100%). Pre-existing; a small
   shared helper would dedupe. Out of scope.
3. **Large file: `deploy.py`** (1743 LOC). Already the 4th-largest source file before this work;
   `_upsert_events` added ~142 lines following the established `_upsert_*` pattern. Splitting deploy.py
   (e.g. an `_upsert` mixin per entity family) is a real big-judgment refactor that predates triggers —
   recorded here, not done.

### This work specifically
- `_upsert_events` mirrors `_upsert_forms`/`_upsert_tables` (the house pattern); no new dupe introduced.
- `_event_entries` matches the surrounding `_*_entries` style (explicit per-relation selects, readable).
  Considered collapsing the repeated schedule/webhook/subs selects; left as-is because it reads cleaner
  in line with the siblings — forcing a clever collapse would fight the established pattern.
- No unused imports/symbols, no dead code, no unrequested fallbacks.

## Recommendation
The two pre-existing dupes (`__init__`, `_read_readme`) are genuinely cheap to fix and would be good
candidates for a future small cleanup — but they're not this feature's regressions, so per the
"report risky / don't touch unrelated code unreviewed" policy they're left for a deliberate pass.
