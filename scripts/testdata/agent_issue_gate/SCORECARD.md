# Agent issue-filing gate — replay scorecard (v1, read-only)

Reviewers: Thomas, Jack. Tool: `scripts/agent_issue_gate.py` (stdlib only).
Fixtures: `pool.json` (1368 frozen issue/PR candidates), `cases.json`
(25 drafts + filing-time cutoffs; per-case candidates are derived as
`createdAt <= filed_at`, self excluded),
`dispositions.json` (later maintainer state, **comparison only, never input**),
`results.json` (machine output), plus `corpus_cases.json` (all 292 drafts,
compact) and `corpus_results.json` (per-issue verdict + disposition).
Replay runner: read-only `gh` fetch + the
real CLI entrypoint; nothing was filed, commented, or published.

Provenance: checklist concepts borrowed from Argo/Airflow/Grafana/Karpenter
practice; analyzer/publisher split borrowed from rakazo
`.github/workflows/issue-triage.yml` (Apache-2.0,
https://github.com/elie222/rakazo/blob/main/.github/workflows/issue-triage.yml
— no code copied). v1 is analyzer-only:
no publisher, no issue-write permission, no webhooks, no human-form changes.

## Commands (repo root, host Python)

```sh
py -m unittest scripts.test_agent_issue_gate -v   # 38 tests, deterministic
# replay (read-only fetch + frozen CLI runs; see tmp builder used for cases.json)
py scripts/agent_issue_gate.py --draft draft.json --candidates cands.json
py scripts/agent_issue_gate.py --draft draft.json --search-error  # unknown, not absent
```

Result: **38/38 unit tests pass.** Targeted replay: **25/25 CLI runs exit 0.**
Full corpus: **292/292 CLI runs exit 0** (all historical issues, `kind=unknown`
for every draft — no label leakage; labels stay comparison-only).

Agent-only evidence field: drafts may carry `evidence: {kinds, provenance,
source}` stating where the evidence came from. Declared kinds count only
when also visible in the body (show, don't claim — a claimed-but-absent
failing test holds the draft with the mismatch named); declared provenance
merges with detected provenance; unknown values warn and are ignored; a
secret-bearing source routes to `private_review`. None of the frozen
historical drafts carry the field, so corpus verdicts are unaffected.

## Full corpus — all 292 historical issues (filing-time drafts only)

Distribution: `ready_to_file` 152 · `needs_evidence` 72 ·
`idea_support_wrong_destination` 61 · `possible_duplicate` 6 ·
`confirmed_duplicate` 1 · `private_review` 0.

| Gate verdict | bug (87) | enhancement (66) | chore (23) | idea (15) | other* (101) |
|---|---|---|---|---|---|
| ready_to_file | 53 | 51 | 3 | 1 | 44 |
| needs_evidence | 34 | 10 | 4 | 3 | 21 |
| idea/support/wrong-dest | 0 | 3 | 15 | 11 | 32 |
| possible_duplicate | 0 | 1 | 0 | 0 | 5 |
| confirmed_duplicate | 0 | 0 | 0 | 0 | 1 |

\* "other" = no bug/enhancement/chore/idea label; includes triage:/priority:-only
and unlabeled issues. Current body snapshots approximate filing-time text;
candidates are cut at filing time; PRs are included as candidates.

What the corpus run caught (fixed in this change, with regression tests):
- **#16 was `private_review` on a `Bearer ...` placeholder.** Bearer-value
  detection now skips placeholders (`...`, `<token>`, `YOUR_*`, `***`);
  real values still route to private review.
- **#958 was `private_review` on "No customer data … are involved."**
  Embargo nouns are now skipped in negated context ("no/without…");
  explicit "do not publish"-style instructions still route to private review.
- **#167 and #181 were buried in `idea`.** Bug-shape detection now accepts
  Symptom/Root-cause structure, `Result:`/`Current behavior` as actual
  behavior, and Expected+Actual without a repro section — those drafts are
  now `needs_evidence` with one concrete question instead of a bucket.
  Feature structure (`## Problem` etc.) is checked first so proposals that
  quote current-vs-expected stay features (covered by a unit test).

Calibration, stated plainly (maintainer decision, Thomas: keep the bar high —
agents filing through this pathway get strict requests, no offense taken):
- Bug-labeled file rate is 53/87 (61%). Of the 34 held, only 5 are thin
  (<600 chars); 29 are substantive reports held on one missing piece —
  mostly a repro section (terse MTG-era titles like the `solution start`
  series #459–#465) or an explicit expected behavior (CodeQL scanner
  findings #213/#214/#217, incident reports #167/#318). This is the
  specified bar (repro, failing test, or redacted incident) working as
  designed — but it is stricter than the humans, who filed and fixed most
  of these without that evidence. Per the maintainer decision above this
  strictness is intentional and stays: the gate's one question is cheap for
  an agent to answer, and anything it holds waits for a human, not a void.
- Duplicates: 1 confirmed true double-file (#1183→#1182); 6 possible, all
  plausibly related (#381→#283 awesome-listing, #1194/#1197/#1199 port-chore
  chain, #1239→PR #297 Azure Blob backend, #1311→#1167 contrast pair).
- `idea`/`chore` labels route to `idea_support` as designed (27 of 38);
  the 3 chore + 1 idea drafts filed `ready` had full bug/feature structure.
  #230 (naming question) lands in `idea` — bucket right, subtype coarse.

## Set 2 — 12-issue historical set (filing-time drafts only)

| Case (public link) | Gate verdict | Maintainer later | Read |
|---|---|---|---|
| https://github.com/gobifrost/bifrost/issues/24 | ready_to_file (safe_repro) | CLOSED, no labels | agree: detailed bug + workaround |
| https://github.com/gobifrost/bifrost/issues/239 | ready_to_file (safe_repro) | CLOSED `bug` | agree |
| https://github.com/gobifrost/bifrost/issues/498 | ready_to_file (safe_repro, failing_test*) | CLOSED, no labels | agree; *failing_test from "regression tests" offer, not an attached test |
| https://github.com/gobifrost/bifrost/issues/726 | ready_to_file | CLOSED `enhancement` | agree |
| https://github.com/gobifrost/bifrost/issues/1169 | ready_to_file | CLOSED `enhancement` | agree |
| https://github.com/gobifrost/bifrost/issues/649 | ready_to_file | CLOSED `enhancement` | **key test passes: concrete request clears the filing bar; accept/decline is a later human decision, out of gate scope** |
| https://github.com/gobifrost/bifrost/issues/120 | ready_to_file | CLOSED, no labels | filed-shape agrees; RFC direction-request — same calibration note as #1340 |
| https://github.com/gobifrost/bifrost/issues/157 | ready_to_file | CLOSED, no labels | filed-shape agrees; detailed RFC — same note |
| https://github.com/gobifrost/bifrost/issues/230 | idea_support (idea) | CLOSED, no labels | bucket right (not a bug/feature); subtype is coarse — it is a question, filed as idea |
| https://github.com/gobifrost/bifrost/issues/418 | idea_support (support) | CLOSED, no labels | held, not filed clean; mixed 3-ask report fits neither subtype — see gap 6 |
| https://github.com/gobifrost/bifrost/issues/318 | needs_evidence ("what should happen instead?") | CLOSED, no labels | rich incident report held on missing expected behavior; correctly NOT flagged as dup of #316 |
| https://github.com/gobifrost/bifrost/issues/316 | needs_evidence ("what should happen instead?") | CLOSED, no labels | deep diagnostic held on the same single question |

## Set 1 — original 13-case replay (unchanged)

| Case (public link) | Gate verdict | Maintainer later | Read |
|---|---|---|---|
| https://github.com/gobifrost/bifrost/issues/1360 | ready_to_file (safe_repro, failing_test) | CLOSED `bug` | agree: filed, fixed |
| https://github.com/gobifrost/bifrost/issues/1353 | ready_to_file (safe_repro, failing_test) | CLOSED `bug` | agree: filed, fixed |
| https://github.com/gobifrost/bifrost/issues/1349 | ready_to_file (safe_repro, redacted_incident) | CLOSED `bug` | agree: filed, fixed |
| https://github.com/gobifrost/bifrost/issues/1243 | ready_to_file (all three evidence kinds) | CLOSED `triage:accepted` | agree: strong report, accepted |
| https://github.com/gobifrost/bifrost/issues/1345 | ready_to_file (safe_repro) | CLOSED `idea` | fileability agrees; kind differs (gate: bug, humans: discord-idea) |
| https://github.com/gobifrost/bifrost/issues/1335 | needs_evidence ("paste the original report…") | CLOSED `idea` | hold; **bury-risk, see below** |
| https://github.com/gobifrost/bifrost/issues/1329 | ready_to_file | CLOSED `enhancement` | agree: filed, shipped |
| https://github.com/gobifrost/bifrost/issues/1292 | ready_to_file | CLOSED `enhancement` | agree: filed, shipped |
| https://github.com/gobifrost/bifrost/issues/1311 | possible_duplicate → #1167 (contrast noted) | OPEN, no labels | held for human, not buried; **pre-fix gate said confirmed — fixed, see below** |
| https://github.com/gobifrost/bifrost/issues/1340 | ready_to_file | OPEN `enhancement` | agrees with humans; **noise-watch, see below** |
| https://github.com/gobifrost/bifrost/issues/1374 | idea_support (support) | OPEN `idea` | agree: support, not a bug |
| https://github.com/gobifrost/bifrost/issues/1352 | idea_support (idea) | OPEN `idea` | agree: interest-check, not fileable |
| https://github.com/gobifrost/bifrost/issues/1183 | confirmed_duplicate → #1182 | CLOSED, no labels | agree: 13-second double-file |

## Where the checker would bury a real problem

1. **#1335 held at the gate.** Title says 422-on-submit bug, but the body is
   "See the linked Discord idea" — zero filing-time evidence. The gate asks
   for one concrete thing (the original report). If the Discord thread
   described a real defect, a strict gate delays a real fix until a human
   pastes the text. Mitigation in v1: the question names exactly what is
   missing; shadow mode means a human still sees the draft.
2. **#1311 was a false `confirmed_duplicate` before the fix in this change.**
   Same author, near-identical title to #1167 — but opposite directions
   ("inbound" vs "outbound"; the draft says so explicitly). The replay caught
   it; the gate now confirms only on identical normalized titles, caps
   near-matches at `possible_duplicate`, and flags contrast pairs
   (inbound/outbound, add/remove, …) for the human. Current replay output for
   #1311 is `possible_duplicate` with the contrast note — held, not buried.

## Where the checker would file noise

- **#1340 passes while deferring 5 decisions** ("RFC design discussion, no
  implementation yet"). It states an operator, a constraint, and an outcome
  sketch, so v1 files it — matching what the humans did (still OPEN
  `enhancement`). A stricter v2 could hold RFCs until one decision is made;
  v1 deliberately does not second-guess RFC-style enhancements.

## Limitations (do not read past these)

- Bodies are current read-only API snapshots, an approximation of filing-time
  text; later edits cannot be excluded. No comments, labels, fixes, or the
  issue itself were used as input; candidates are cut at filing time.
- Search failure means unknown, never "no duplicate". Matching is
  title-similarity only (no semantic match); #318/#316 correctly stay
  distinct, but near-identical titles with different bodies would over-match.
- Repro claims are trusted and labeled (observed/reproduced/inferred), not
  verified. #498's `failing_test` kind comes from the author offering
  regression tests, not from an attached failing test.
- `private_review` is covered by unit tests only — no real secret-bearing
  case exists in either replay set (deliberately never constructed).
- Mixed multi-ask reports (#418) and pure questions (#230) land in
  `idea_support` with coarse subtypes; neither subtype fits #418 well.
- Prioritization, accept/decline (#649), and execution are out of scope;
  the gate emits no priority.

## Remaining gaps (v2, not this change)
1. Publisher side (restricted, separate) — explicitly not built.
2. Live read-only candidate fetch (`gh search issues/PRs`) as a caller-side
   wrapper; the gate itself stays offline/deterministic.
3. Filing-time body snapshots (event history) instead of current-body
   approximation.
4. Semantic near-duplicate matching; body-text duplicate signals.
5. RFC/decision-open handling policy (hold vs file with a flag).
6. Mixed-report handling: one draft with N asks should get a "split or pick
   one" question instead of an idea/support bucket (#418).
7. Scanner-output evidence: CodeQL-style findings (#213/#214/#217) carry a
   tool verdict plus an affected path but no repro — v2 could accept
   "scanner finding + code path" as observed evidence instead of asking for
   expected behavior.
