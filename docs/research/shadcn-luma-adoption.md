# Adopting shadcn's Luma/Rhea design language in Bifrost

**Date:** 2026-06-11 · **Spike branch:** `spike/shadcn-luma` (worktree `.claude/worktrees/shadcn-luma-spike`, based on `ui/history-facelift`) · **Status:** timeboxed spike — throwaway branch, nothing merges as-is.

## TL;DR

- **There IS a retrofit path for existing projects, and it's small.** Set `"style": "radix-rhea"` in `components.json`, run `npx shadcn@latest add <component> --overwrite`, install the consolidated `radix-ui` package, and inline ~95 lines of shared CSS (`shadcn/tailwind.css`) + the new radius scale into `index.css`. The registry serves the Rhea-styled component source to existing projects — verified byte-identical to what `shadcn/create` scaffolds.
- **Rhea over Luma.** Rhea is the compact variant (default button h-8 vs Luma's h-9; tighter gaps and paddings throughout) and is explicitly "built for focused product interfaces." Bifrost is a dense MSP ops tool — Rhea is the obvious fit. Same geometry/rounding language otherwise.
- **The swap is surprisingly low-fallout.** 14 core primitives swapped on this spike; the ONLY tsc breakage in ~252 importing files was Bifrost's custom badge `warning` variant (re-added in Rhea idiom). Visual drift is the real cost, not compile fallout.
- **Effort for full adoption: roughly 1.5–3 engineer-weeks**, dominated by visual reconciliation of dense screens and the ~20 custom composite components, not by the mechanical swap (~1 day).

---

## 1. The real adoption mechanism (investigated empirically)

### What shadcn shipped

- **Luma (March 2026):** new design language — rounded geometry (`rounded-2xl` buttons/badges, multiplicative radius scale up to `--radius-4xl`), soft elevation, soft-tint destructive (`bg-destructive/10 text-destructive` instead of solid red), breathable spacing.
- **Rhea (May 2026):** "a more compact Luma. Smaller spacing. Denser surfaces." Deliberately a **separate style**, not a `--spacing` multiplier tweak, so Tailwind utilities keep their normal meaning. Both ship for Radix and Base UI (`radix-rhea`, `base-rhea`, …).
- **`shadcn eject` (May 2026):** inlines `shadcn/tailwind.css` into your global CSS and removes the `shadcn` package dependency.

### How existing projects adopt it (verified with shadcn CLI v4.11.0)

The changelog only advertises `shadcn/create` for new projects, but the CLI has a real retrofit path:

1. **`components.json`:** change `"style": "new-york"` → `"style": "radix-rhea"` (plus `iconLibrary`, `menuColor`, `menuAccent` keys used by new-era styles). The registry keys component source off this field.
2. **`npx shadcn@latest add <names> --overwrite`** then fetches **Rhea-styled source** — verified: the `button.tsx` fetched into Bifrost was byte-identical to one scaffolded by `shadcn/create` with the Rhea preset (`b27GcrRo` = style `radix-rhea`, neutral, lucide, Inter).
3. **Two gaps the CLI does NOT handle on the retrofit path** (it handles both on `init`):
   - **Deps:** new components import from the consolidated **`radix-ui`** package (`import { Slot } from "radix-ui"`), not `@radix-ui/react-*`. `add --overwrite` did not install it; we did `npm install radix-ui` manually. The old per-package `@radix-ui/react-*` deps stay for not-yet-swapped components.
   - **CSS:** new components depend on `@import "shadcn/tailwind.css"` — ~95 lines of `@custom-variant` definitions (`data-open`, `data-checked`, `data-active`, …), accordion keyframes, and a `no-scrollbar` utility. Without it, state-dependent styling silently breaks (e.g. Switch never shows its checked color). The package that provides it is the **full 6 MB shadcn CLI** with ~30 runtime deps, so we **inlined the 95 lines into `index.css`** instead (exactly what `shadcn eject` produces). Trade-off: re-sync manually if future registry components grow new variants.
   - Also: the new-york-era additive radius scale (`--radius-sm: calc(var(--radius) - 4px)` …) must be replaced with Rhea's multiplicative one (`0.6x / 0.8x / 1x / 1.4x / 1.8x / 2.2x / 2.6x` for sm→4xl) — new components use `rounded-2xl`/`rounded-3xl`, which otherwise fall back to Tailwind defaults and look wrong.
4. **`npx shadcn@latest apply b27GcrRo`** is the official one-shot "apply preset to existing project" command. We deliberately did NOT use it: a preset bundles **theme** (it would stomp Bifrost's teal brand tokens with neutral) and **font** (Inter). The manual path above adopts the geometry/components while keeping our palette. `--only theme|font` exists but there is no `--only style`.
5. **`init --preset b27GcrRo --reinstall --force`** also exists ("re-install existing UI components") — same theme-stomping caveat; untested here.

**Verdict: no reference-app porting needed.** The registry fully supports retrofits; the work is `components.json` + `add --overwrite` + two small manual patches. The components are plain vendored TSX as always — no runtime coupling to the `shadcn` package after inlining the CSS.

## 2. Luma vs Rhea for Bifrost

**Recommendation: Rhea.**

| | Luma | Rhea |
|---|---|---|
| Default button | h-9 | **h-8** |
| `sm` button | h-8 | **h-7** |
| Positioning | "calm, breathable" marketing/site feel | "focused product interfaces" |
| Geometry | rounded-2xl, soft elevation | same language, tighter |

Bifrost's surfaces are run-history tables, config forms, execution logs — density is a feature. Rhea also lands *closer* to our current sizing (new-york default h-9 → Rhea h-8 is one notch denser; Luma would keep h-9 but inflate paddings/gaps). Both share the new rounding/soft-tint language Jack liked, so choosing Rhea loses nothing visually.

One real density note: Rhea's **table** rows and **dropdown items** are tighter than our current new-york versions. On data-heavy pages this reads as "more rows per screen" (good) but makes any hand-tuned row heights in custom components (data-table, LogsTable) look mismatched until reconciled.

## 3. What this spike actually did

- `components.json` → `radix-rhea` (+ `iconLibrary`/`menuColor`/`menuAccent`).
- Swapped **14 primitives** via `shadcn add --overwrite`: button, card, badge, input, select, dropdown-menu, switch, checkbox, dialog, sheet, tabs, separator, skeleton, table.
- `npm install radix-ui` (lockfile regenerated under node:20 npm — host npm v11 writes a lockfile the dev-container's npm v10 rejects).
- `index.css`: inlined `shadcn/tailwind.css` v4.11.0; replaced radius scale with Rhea's multiplicative one. **Kept Bifrost's teal brand tokens and dark-mode palette untouched.**
- Re-added Bifrost's one lost API extension: badge `variant="warning"`, restyled to Rhea's soft-tint idiom (`bg-amber-500/15 text-amber-700`). That was the **only** tsc error across 252 files importing ui components. New primitives are supersets otherwise (button gained `xs`/`icon-xs`; old `icon-sm`/`icon-lg` preserved; dialog kept `showCloseButton`; React 19 means the forwardRef→function change is transparent).
- tsc + production vite build pass. Lint clean (one pre-existing warning in FormRenderer, unrelated). Vitest NOT run (spike); expect some class-assertion churn in `*.test.tsx` for the swapped primitives.

## 4. Effort estimate for real adoption

| Workstream | Size | Notes |
|---|---|---|
| Tokens + 14 core primitives (this spike, redone cleanly) | **~1 day** | Mechanical; verified low-fallout |
| Remaining ~21 vendored primitives (accordion, alert, avatar, calendar, command, context-menu, form, hover-card, label, pagination, popover, progress, radio-group, slider, sonner, textarea, toggle, toggle-group, tooltip, alert-dialog, collapsible) | **1–2 days** | Same `add --overwrite` + diff-for-local-extensions loop; command/calendar/form are the fiddly ones |
| ~20 custom composites (chat-composer, expression-editor, data-table, context-viewer, tiptap-*, combobox/multi-combobox, date-time-picker, tags-input, …) | **3–5 days** | These hand-roll paddings/radii copied from new-york-era primitives; each needs a visual pass to stop looking like a different app |
| App-code visual reconciliation (~252 files import ui/, ~996 import statements, 234 files touch a swapped primitive) | **3–7 days** | Not compile errors — *drift*: call sites that hard-code `rounded-md`, `h-9`, `shadow-xs`, or compensate for old paddings now fight the new geometry. The ~310 drifted call sites from the facelift audit live here |
| Test churn + Playwright screenshot re-baselining | **1–2 days** | |

**Total: ~1.5–3 engineer-weeks**, heavily parallelizable page-by-page after the primitive layer lands.

## 5. Risks

1. **Visual regressions at drifted call sites.** Biggest one. Anywhere app code overrode primitive classes (`className="h-9 rounded-md ..."` on a Button) now produces franken-styling. Mitigation: land tokens+primitives behind the facelift branch and reconcile page-by-page with screenshots, exactly like the History facelift flow.
2. **Density shift on tables/forms.** Default control height drops h-9→h-8; selects/inputs/dropdown items tighten. Mostly desirable for Bifrost, but forms built around old heights can look unbalanced mid-migration.
3. **Destructive styling semantics changed.** Rhea's destructive button/badge is a soft tint, not solid red. Anywhere we rely on "big red button" affordance (delete confirmations) should be reviewed; solid style is recoverable via a local variant if wanted.
4. **Two Radix dep trees during migration** (`radix-ui` + `@radix-ui/react-*`). Slight bundle overhead until the long tail is swapped and old deps dropped; not a correctness risk (verified compiling/building).
5. **Inlined shared CSS can drift** from registry expectations on future `add`s. Cheap to re-sync (95 lines, versioned comment in index.css); alternatively accept the `shadcn` package as a dependency.
6. **Custom composites are the long tail.** chat-composer/expression-editor/data-table embed new-york-era spacing constants; until they're done the app is visibly two-toned.
7. **Theme presets would stomp brand tokens** — never run `shadcn apply <preset>` or `init --reinstall` without `--only`-style care; the manual components.json path is the safe one.

## 6. Suggested rollout

1. **Phase 0 (this spike, redone on a real branch):** `components.json` flip, `radix-ui` dep, inlined shared CSS + radius scale, 14 core primitives, restore local extensions (badge `warning`). Gate: tsc + build + vitest green, no app-code edits yet.
2. **Phase 1:** remaining ~21 vendored primitives, same loop. Re-baseline Playwright screenshots once.
3. **Phase 2:** page-by-page reconciliation **aligned with the in-flight UI Facelift** — History/execution-details first (already under active review), then Dashboard, Settings, agents. Each page PR: fix drifted call-site overrides + adjust the custom composites that page uses.
4. **Phase 3:** sweep the long-tail composites (chat, tiptap, expression-editor), drop unused `@radix-ui/react-*` deps, decide on Inter (`@fontsource-variable/inter`) as a deliberate separate call.

## Appendix: artifacts

- Reference Rhea app (scaffolded via `shadcn/create` preset `b27GcrRo`): `/tmp/shadcn-spike/rhea-ref` (throwaway).
- Screenshots: `/tmp/shadcn-spike/*.png` — History list, execution details + slideout, Dashboard, Settings (dropdown open, switches visible).
- Preset decode: `npx shadcn@latest preset decode b27GcrRo` → style `rhea`, baseColor neutral, lucide, Inter.
