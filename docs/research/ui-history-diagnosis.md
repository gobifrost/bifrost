# History page — designer-eye diagnosis

Date: 2026-06-11 · Page: `client/src/pages/ExecutionHistory.tsx` · Before: `/tmp/ui-history/before.png`

## The page's job

For an MSP operator the History page answers, in priority order:

1. **Did my automations run?** (a glanceable feed of recent activity)
2. **What failed, and why?** (the exceptional rows must pop; the reason must be readable without hunting)
3. **Drill in** (one obvious path to the full execution record)

It is a *time-ordered feed of mostly-successful events*. The correct mental model is a
monitoring feed (like a CI run list or an alerting timeline), rendered as a dense table
for scannability — not a generic CRUD grid where every column has equal weight.

## Named causes of the "off" feeling

### 1. Inverted status hierarchy — success screams, failure whispers
Thirteen solid-green `Completed` pills dominate the viewport; bright green is the
loudest color on the page and it's attached to the *non-event*. Meanwhile the one thing
the operator came to find — **why** `offboard_user` failed — is hidden behind a 16px
hover-only info icon. A designer renders the common case quietly (outline badge, muted
check) and spends color on the exceptions (failed/timeout filled red, error text inline).

### 2. Two timestamp columns burn ~40% of the grid to say the same thing
"Started At" and "Completed At" each render a full two-line datetime that differs by
seconds; Duration is *also* a column, so Completed At is pure redundancy. The wrap to
two lines makes every row tall and ragged. One "Ran" column (relative time, absolute on
hover) plus a right-aligned Duration carries the same information in a quarter of the
space and reads instantly.

### 3. No temporal landmarks — a history page with no sense of time
A run history is a feed, but all rows render identically with no grouping. The eye has
to parse a full datetime per row to know "was this today?". Day separator rows
("Today", "Yesterday", date) give the timeline quality a history page needs while
keeping the table's scannability.

### 4. Repeated identical metadata as loud chrome
Every row carries an identical outlined "Provider" org badge — zero bits of
information rendered sixteen times (it only matters when orgs *differ*). Likewise the
per-row Eye button duplicates the row's own click affordance, and the centered
pagination footer renders "Previous 1 Next" even when there is only one page. All of it
is chrome that competes with data.

### 5. Filter-bar soup with no grouping or order
One row holds: an admin-only "Logs View" mode *switch* (leftmost — the position of
highest priority), a small search box, workflow combobox, date range, a "Show Local
Executions" checkbox, and an org select — all equal weight, mode toggles interleaved
with filters. Designers group: primary search first and widest; entity filters
together; the rare mode switch and debug toggles demoted to the end.

### 6. Missing/weak states
- **Error state: none.** If `GET /api/executions` fails the page silently shows the
  empty state ("Execute a workflow to see it appear here") — actively misleading.
- **Loading**: a centered spinner that collapses the layout, then the table pops in.
  Skeleton rows hold the layout.
- **Empty**: one generic message regardless of context. "No executions found — execute
  a workflow" is wrong when the real cause is the Pending tab / search term / date
  filter; a filtered-empty needs "nothing matches" + a clear-filters action.

### 7. The header line narrates instead of informing
"View and track workflow execution history" tells the operator what the page is
(they know — they clicked History). That line is the natural home for the rollup the
job actually needs: how many runs, how many failed.

## Candidates for a future light design system (not built here)

- **The elevation ladder** (replaces the earlier "recess darker" convention, which was
  wrong — Jack 2026-06-11, anchored on ui.shadcn.com's home dashboard: "the main card
  is always the darkest, then subcards get progressively lighter"). Nesting always
  moves AWAY from the base, never back toward it.
  - **Dark** (each nested layer LIGHTER): `background` oklch(0.145) → `card`/`popover`
    oklch(0.205) → step-1 nested block `bg-muted/50` over the surface (≈0.237) →
    step-2 (code/pre, panel headers inside step-1) full `bg-muted` oklch(0.269).
    Reference sampled off ui.shadcn.com dark: background lab(2.75) → card lab(7.78) →
    muted/secondary lab(15.2) → accent lab(27).
  - **Light** (each nested layer GREYER): `background`/`card` white → step-1
    `bg-muted/50` (≈0.985) → step-2 `bg-muted` oklch(0.97). Reference: white →
    muted lab(96.5) → input lab(90.9).
  - **Chrome bands** (sticky table header/footer, slideout sticky header): the base
    `background` token — a near-black band against the lighter `card`/`popover` body
    in dark ("that header used to be basically black… it looks way better"), clean
    white + border in light. Bands are app chrome showing the base through, not a
    nesting step.
  - **Grouping bands** (day-separator rows inside a table body): half-step between
    the chrome band and the body — `dark:bg-background/50` over the card (≈0.175),
    so dark reads band (0.145) < day row (≈0.175) < body (0.205); light keeps the
    soft `bg-muted/40` grey.
  - Edges on nested blocks: `ring-1 ring-foreground/5`, no border-in-border.
- **Page header pattern**: title + optional mode toggle + summary line + actions, with
  fixed slots, so every page stops hand-rolling its own.
- **Status badge philosophy**: quiet-success/loud-failure variants as a shared
  component (today `ExecutionStatusBadge` and inline `getStatusBadge` duplicate each
  other, both loud-green).
- **List vs table vs feed selection**: history/audit/event pages share the
  "time-grouped dense table" need (day separators, relative time + hover absolute).
- **Standard empty/loading/error triad** for list pages, with a filtered-empty variant
  that offers clear-filters.
- **Density & column budget**: relative time + tooltip as the default for timestamps in
  lists; absolute datetime only in detail views.

---

# Part 2: Execution slideout (drawer) and details page

Date: 2026-06-11 · Surfaces: `ExecutionDrawer.tsx` + `ExecutionDetails.tsx` (embedded and
full-page modes) and the shared panels in `client/src/components/execution/`.
Before shots: `/tmp/ui-history/slideout-before*.png`, `/tmp/ui-history/details-before*.png`.

## The jobs

- **Slideout**: quick triage without losing your place in the list — did it fail, why,
  what went in/out, glance at the logs. Density and a stable identity header matter;
  ceremony does not.
- **Details page**: the full forensic record — complete logs, timeline, parameters,
  result, resource/AI usage. Hierarchy must follow the forensic question order:
  *what happened → why → with what inputs → full trace.*

## Named causes — slideout

### S1. Tracebacks rendered as N bordered log rows
The single worst offender. A Python traceback is ONE artifact, but each line arrives as
its own log entry, so a failed run renders ~10 ruled rows each repeating
"8:42:47 AM TRACEBACK" before the message fragment. The eye has to strip the repeated
chrome from every line to reconstruct the stack. Consecutive traceback lines must
coalesce into one timestamped block.

### S2. Result panel buries data under chrome, then truncates it invisibly
"Result" + "Workflow execution result" narration + "Viewing 6 parameters" + a Tree View
button = four lines of ceremony before any data. Then nested objects render in JSON
blocks hard-capped at 8rem with overflow *hidden* — the Site object's
`wifi_ssids` is cut mid-token with no indication there's more and no way to scroll.
Truncation without affordance is data loss.

### S3. Loud-green badge and inconsistent section rhythm
The drawer still uses the solid-green `ExecutionStatusBadge` the list page just
retired — success screams again. Sections use three different header treatments
(Card title for Result, bare `h4` for Input Parameters, shaded bar for Logs), so the
panel has no consistent vertical rhythm to scan by.

### S4. Missing affordances: no copy on error or logs (embedded), spinner loading
The error block — the thing an operator most wants to paste into a ticket — has no
copy button; the embedded logs header has none either (the full-page variant does).
Loading is a centered spinner that collapses the layout instead of a skeleton.

## Named causes — details page

### D1. The error lives in the sidebar; dead space leads the page
On a failed run the left (primary) column leads with a full "Result" card containing
only "No result returned" — a card of dead space — while the actual answer ("why did
it fail") sits in the right sidebar, below an "Execution Status" card, often below the
fold. The error must be the first full-width element in the content column.

### D2. A giant status card duplicates the header badge
"Execution Status" spends an entire sidebar card on a 48px icon + the same badge shown
20px away in the page header. Pure duplication rendered as decoration.

### D3. Verbose label-stacked metadata instead of a compact definition list
"Workflow Information" stacks label-over-value pairs (including the workflow name,
already in the header) and renders Started At + Completed At as two full datetimes —
the same two-timestamps cause named on the list page. A compact label/value list with
relative times (absolute on hover) and an explicit Duration row carries more in half
the height.

### D4. Narration under every title
"Workflow execution result", "Python logger output from workflow execution",
"Workflow parameters that were passed in" — every card explains the obvious instead of
informing (e.g. the log line count).

## Generalizable patterns for the future light design system

- **Log/console block**: monospace, hover rows, no per-line rules, coalesced
  multi-line artifacts (tracebacks), copy button — reusable for any log surface.
- **Triage drawer anatomy**: identity header (name + status + meta) → error → output →
  inputs → trace, with one consistent section-label treatment.
- **Definition-list metadata card** to replace label-stacked Cards.
- **"Quiet success" status badge** is now needed on a third surface — it should live in
  `components/execution/` as the canonical badge (done in this pass).
