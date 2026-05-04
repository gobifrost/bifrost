# Policy Editor Redesign

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. The branch this lands on is `feat/table-access`; the existing components live at `client/src/components/tables/PolicyEditor*.tsx` and `client/src/components/tables/PolicyReferencePanel.tsx`.

## Why

The current PolicyEditor is half-and-half: `name` / `description` / `actions` are form fields, but `when` — the actual hard part — is a Monaco JSON blob inside each row. So users learn the AST anyway, just one row at a time. Worse, the CLI and `.bifrost/tables.yaml` already treat the entire `TablePolicies` value as a single document, and roundtripping a complex policy set means flipping between the rows UI and a YAML file in a side window.

The user's call: **three tabs, all bound to the same `TablePolicies` state** — **Form** (a fully graphical builder that covers the `when` AST too, no Monaco-in-form), **JSON** (single Monaco for the whole document), **YAML** (single Monaco for the whole document). The reference panel stays open as the learning surface and grows from term/def lookup into copy-pasteable full-policy examples covering every operator and pattern.

The Form tab needs to be space-conscious — the existing per-row layout sprawls and pushes the actual rule offscreen. A compact, dense layout for the rule list, with the `when` builder inline and collapsible. No Monaco anywhere in the Form tab; if a user wants to type raw, that's what JSON/YAML are for.

## Scope

**In:**
- Replace `PolicyEditor.tsx` with a tabbed view (`Tabs` from shadcn). Three tabs: **Form**, **JSON**, **YAML**. All three bind to a single `TablePolicies | null` state held by the parent.
- Form tab is fully graphical for both the policy metadata AND the `when` AST. Compact layout. No Monaco editor in the Form tab.
- JSON / YAML tabs are single Monaco editors, schema-bound to `policy-schema.json`.
- Switching tabs round-trips through the parsed AST. Form tab has no parse error (the UI can only construct valid trees by construction). JSON / YAML refuse to switch when the text is unparseable, surfacing the parse error inline.
- The "Insert template" select stays as a top-bar action; templates append to the AST and all three tabs reflect the change.
- The "Reference" button stays; the panel grows with full example policies (see "Reference panel content" below).

**Out (delete entirely):**
- `PolicyEditorRow.tsx` in its current shape (Monaco-per-when). The Form tab gets a new compact row component with a graphical `when` builder.

**Not changing:**
- `policy-schema.json` (already authoritative for Monaco IntelliSense).
- `policy-templates.ts` constants (still used by the Insert template select).
- The empty-state hint ("No policies. Without a policy, only the table owner and platform admins can access rows.").
- The parent `TableDialog` contract — still passes `value: TablePolicies | null` and an `onChange` setter; semantics unchanged.
- The `policies.py` Pydantic AST validator (server-side authoritative).

## The `when` AST builder (Form tab)

The whole point of going graphical-everywhere is that `when` becomes constructible without typing JSON. The AST has a closed grammar:

| Node kind | Shape | Operands |
|---|---|---|
| Literal | string / number / bool / null | — |
| Reference | `{user: <field>}` | enum picker over 6 known user fields |
| Reference | `{row: <field>}` | text input (column or `data.<path>`) |
| Logical | `{and|or: [...]}` | 2+ child nodes |
| Logical | `{not: ...}` | 1 child node |
| Comparison | `{eq|neq|lt|lte|gt|gte: [a, b]}` | exactly 2 operands |
| Set | `{in: [a, [v1, v2, ...]]}` | 1 operand + literal list |
| Null | `{is_null: a}` | 1 operand |
| Function | `{call: <name>, args: [...]}` | enum picker over registered functions |

Builder shape:
- One root node, recursive. Each node renders as a horizontal pill:
  `[op-picker ▾] [operand 1] [operand 2] ...` with `[+]` to add operands where the op allows it and `[×]` to remove the node.
- Operand slots are themselves pickers: `[Literal | User ref | Row ref | Expression]`. Choosing "Expression" turns the slot into a nested node.
- `null` for the whole `when` is the "always true" rule — explicit toggle: `[● Always true | ○ Build expression]` at the top of the `when` builder.
- The literal-list operand of `in` gets a chip-style multi-input.
- Indent depth has a soft cap of 8 visually; deeper trees collapse with a "show more" disclosure (validator's hard cap is 64 per the hardening branch).

Compact-mode rules:
- Default density: 28-32px row height, no per-policy borders, alternating background.
- Action checkboxes inline (one row of four: Read / Create / Update / Delete).
- Description is single-line; expands on focus.
- The `when` builder sits below the metadata row, indented; collapses to a one-line summary (`when: row.created_by = user.user_id`) when the row is collapsed.
- Each policy row has a chevron to collapse to a one-line summary.

## Tasks

### Task 1: Wire shadcn `Tabs` and the three-tab shell

**Files:**
- Modify: `client/src/components/tables/PolicyEditor.tsx` — full rewrite.
- Verify: `client/src/components/ui/tabs.tsx` exists (shadcn Tabs primitive). `js-yaml` is in `client/package.json` (`^4.1.0` confirmed).

Layout:

```
┌─ Policies ────────────────── [Insert template ▾] [Reference] ─┐
│ ┌─ Form ─┬─ JSON ─┬─ YAML ────────────────────────────────┐    │
│ │                                                         │    │
│ │  (tab content fills available height; min 320px)        │    │
│ │                                                         │    │
│ └─────────────────────────────────────────────────────────┘    │
│ Parse error: <message>             (only when invalid in code) │
└────────────────────────────────────────────────────────────────┘
```

Source of truth: a single `TablePolicies | null` from the parent. Tabs render different views of it; edits in any tab funnel back through `onChange` after producing a valid AST.

State model:
- `value: TablePolicies | null` (from parent).
- `activeTab: "form" | "json" | "yaml"`.
- `jsonText`, `yamlText`: per-tab text buffer for editor mode (ignored by Form tab).
- `jsonParseError`, `yamlParseError`.
- `lastSyncedJson`, `lastSyncedYaml`: tracks canonical text to avoid the reset-loop the old PolicyEditorRow had.

Tab-switch behavior:
- **Any → Form**: parse the active tab's text (if any), set the AST, switch.
- **Form → JSON / YAML**: reserialize the AST to the destination tab's grammar, switch (no parse risk).
- **JSON ↔ YAML**: parse current → reserialize → switch. Fail = stay + show error.

### Task 2: Form tab — compact rule list + `when` builder

**New file:** `client/src/components/tables/PolicyFormView.tsx`. Renders the rule list.

**New file:** `client/src/components/tables/PolicyExpressionBuilder.tsx`. Recursive component that renders an `Expr` node as the pill / nested layout. Emits `onChange(nextExpr)` to its parent.

Sub-files (judgment call, keep flat if files grow):
- `client/src/components/tables/expr-shapes.ts`: pure helpers — `kindOf(node)` returning `"literal" | "row-ref" | "user-ref" | "and" | ...`, `defaultNodeForKind(kind)`, `summarize(expr)` for the collapsed one-line preview.

Spec:
- Each policy is a row in `PolicyFormView`. Row fields: chevron (collapse), name (text input, narrow), actions (4 checkboxes), description (single-line input, expands on focus), trash icon. When collapsed: just `chevron + name + actions + when-summary + trash`.
- Expanded: the description + `PolicyExpressionBuilder` for `when`.
- `PolicyExpressionBuilder` always-true toggle at the root: when on, `when: null` is emitted and the builder body is hidden.
- Operand slot kind picker: small dropdown with the four kinds. Switching kind resets the slot to the kind's default (`""` for row-ref, first known user field for user-ref, `null` for literal, the default node — `{eq: [null, null]}` — for nested expression).
- For the `in` operator's literal-list operand: chip input. Empty list shows a hint that the validator rejects empty `in` lists.

The builder must produce **only valid AST shapes by construction**. There is no escape hatch for invalid input in the Form tab. (Users who need that go to JSON/YAML.)

### Task 3: JSON / YAML tabs — single Monaco each

**New file:** `client/src/components/tables/PolicyCodeView.tsx`. Renders one Monaco editor for the whole `TablePolicies` document; takes a `mode: "json" | "yaml"` prop.

Use `js-yaml` (already a dep) for YAML side. Use `JSON.stringify(value, null, 2)` for JSON side.

Round-trip rules:
- Trailing whitespace differences shouldn't trigger reset loops — track `lastSynced` per tab, same pattern the old row had.
- A `when: null` rule serializes as `when: null` in YAML (explicit), so the user can see the "always-true" rule.
- Anchors / aliases / multi-document YAML are not supported. Use `yaml.load` with `safe` schema only. Reject documents whose root is anything other than `{policies: [...]}` (with a clear error).
- Schema-bind `policy-schema.json` to JSON tab via the existing Monaco helper. (YAML schema binding via `monaco-yaml` is out of scope unless the package is already in deps — verify before adding.)

### Task 4: Reference panel content

**File:** `client/src/components/tables/PolicyReferencePanel.tsx`.

Today: term/def list (USER fields, ROW fields, Functions, Operators).

After: keep those, but add a **Worked examples** section with copy-pasteable full policies for each pattern. The list should let a user hit Reference, find a pattern, copy, and paste into JSON or YAML tab.

Lift canonical examples from `docs/superpowers/specs/2026-04-30-table-policies-design.md:13-43` plus operator-by-operator coverage. Minimum set:

- `admin_bypass` (already a template)
- `own_row` (already a template)
- `own_org` (already a template)
- `role_gated_read` (already a template)
- **Read-only for finalized rows**: `not` + `eq` against a row field
- **Range comparison**: `gte` / `lte` on a numeric field, e.g. `data.amount`
- **Membership**: `in` against a literal list, e.g. `data.status in ["active", "pending"]`
- **Null check**: `is_null` and `not + is_null` for "is set"
- **Compound AND**: two clauses combined
- **Compound OR**: alternative grants
- **Nested AND/OR**: showing precedence + indentation
- **Function call**: `has_role` with role name and with role UUID (string-compared)
- **Manager-reads-reports**: denormalized `manager_user_id` row field (lifted directly from the design doc)
- **Cross-org provider read**: `or` between own-org and platform-admin (covers a common provider scenario)

Each example block:
```
<Heading>
<one-line description>
[Copy] button (copies as JSON; YAML tab can paste directly because it's a JSON superset; Form tab can paste-and-import — see Task 5)
<code block: pretty-printed JSON, plain <pre> with light syntax color>
```

Also include a **Footguns** subsection covering the gotchas already in the design doc:
- `null` propagation: `eq` against null returns false, not null. Use `is_null`.
- Validator rejects `eq: [..., null]` literal at write time — use `is_null` (per Task B in the hardening branch).
- Empty `in` lists are rejected.
- `not + is_null` is the "is set" idiom.
- `eq` on a missing JSONB path is false, not error.

### Task 5: Paste-and-import into Form tab (nice-to-have, gate at the end)

If time permits in this session: add a "Paste JSON" affordance to the Form tab so users can drop a Reference example directly into a Form rule rather than switching to JSON tab first. Out of scope if it adds >1 hour. (The JSON/YAML tabs already cover the use case.)

### Task 6: Tests

**File:** `client/src/components/tables/PolicyEditor.test.tsx` — full rewrite.

**Form tab** (most coverage here, since this is the new surface):
- Empty state: `value=null` shows the empty hint and an empty rule list.
- Rendering: a `TablePolicies` with two policies renders two collapsed-by-default rows; expanding shows the description + when builder.
- Add policy: clicking "Add policy" appends `{name: "new_policy", actions: ["read"], when: null}` and `onChange` receives it.
- Insert template appends to the policies array.
- Editing name / description / actions emits the updated `TablePolicies`.
- `when` builder: the always-true toggle hides the body and emits `when: null`.
- `when` builder: building `{eq: [{row: "created_by"}, {user: "user_id"}]}` step-by-step — pick op, pick operand kind, pick fields — emits the matching AST.
- `when` builder: changing operand kind resets to the kind's default.
- `when` builder: the `in` operator's literal-list slot accepts chip input, rejects empty list visually.
- Remove row → if last, `onChange(null)`; otherwise drops the row.

**JSON / YAML tabs**:
- Initial render with policies: JSON tab shows pretty-printed JSON of `value`; YAML tab (after click) shows the YAML serialization.
- Edit JSON, parse, emit: typing valid JSON triggers `onChange` with the parsed `TablePolicies`.
- Edit YAML, parse, emit: same on the YAML tab.
- Tab switch round-trip Form → JSON → YAML: same AST through all three tabs.
- Tab switch blocked on parse error in code tabs: type invalid JSON, click YAML tab → tab does not switch, error message visible.
- Empty content collapses to null: clearing the editor emits `onChange(null)`.

**Reference panel**:
- Reference button opens the panel.
- Worked-examples section renders at least 10 example headings.
- Copy button for an example is present (don't assert clipboard write — that's flaky in jsdom; assert the button exists and the example code block is visible).

Stub Monaco the same way the existing test file does (`vi.mock("@monaco-editor/react")` returning a `<textarea>`). The Form tab uses no Monaco, so its tests run without the stub plumbing.

### Task 7: Manual smoke + browser screenshots

Boot the dev stack, open Tables → New table → Policies.

1. Empty state, Form tab active by default → see the hint.
2. Insert template `own_row` → Form tab shows a collapsed row whose summary reads "row.created_by = user.user_id". Expand → see the `when` builder reflecting that exact tree.
3. Build `data.status in ["active", "pending"]` from scratch in the builder → switch to JSON → see the literal AST.
4. Switch to YAML → see indented YAML.
5. Edit YAML by hand to add a `not + is_null` rule → switch back to Form → new row appears with the correct builder state.
6. Type invalid JSON → click YAML → tab doesn't switch, inline error visible.
7. Reference panel → scroll examples → copy a `has_role` example → paste into JSON tab → switch to Form → row materializes.
8. Save → confirm `tables.access` JSONB matches the editor content (via `bifrost tables get <name> --json`).

Capture screenshots of (1), (2 expanded), (3), (4), (6), (7) into `~/Sync/Screenshots/policy-editor-redesign-N.png` for the PR description.

## Verification

```bash
# From the worktree root
cd /home/jack/GitHub/bifrost/.worktrees/table-access

# Frontend checks (dev stack must be up for type generation)
cd client
npm run lint
npm run tsc
npx vitest run src/components/tables/

# Backend untouched, but rerun the existing policy e2e to confirm
# the editor changes don't regress the policy contract:
cd ..
./test.sh tests/e2e/platform/test_policies.py
./test.sh tests/e2e/platform/test_tables.py
```

## Out of scope (explicit "no")

- **Diff view**: showing the policy delta vs. the saved version. Useful, separate plan.
- **Linting / autoformatting beyond Monaco's built-in JSON/YAML formatting**: e.g. canonical key order, sorting policies by name. Defer.
- **Server-side validator improvements**: the AST validator in `api/src/models/contracts/policies.py` and the JSON Schema in `client/src/lib/app-sdk/policy-schema.json` are already shipping. If a Reference panel example exposes a validator gap, file a follow-up plan; don't fix it inline.
- **Drag-to-reorder rules**: the policies array is order-insensitive (additive OR resolution). No need.
- **Drag-to-nest in the `when` builder**: building a nested expression goes through the operand-kind picker. A drag affordance is a v2 concern.
- **`monaco-yaml` schema binding** unless the package is already in deps. Plain Monaco YAML mode is acceptable; users can flip to JSON tab for full IntelliSense.

## Notes for the next session

- The branch `feat/table-access` is currently 8 commits past where the hardening landed. Make sure the dev stack and test stack are both up for the worktree before starting type-gen.
- `PolicyEditor.test.tsx` already mocks Monaco and Radix Select — reuse those mocks for the JSON/YAML tab tests.
- `PolicyEditorRow.tsx` is the only consumer of the per-row Monaco; deleting it leaves no orphans elsewhere (verify with `grep -r "PolicyEditorRow" client/`).
- The authoritative AST surface lives at `api/src/models/contracts/policies.py` (`KNOWN_USER_FIELDS`, `_ALL_OPS`) and `api/shared/policies/functions.py` (`FUNCTIONS`). Mirror these constants on the client — drift is the failure mode. Consider generating them from the Python source if the duplication starts mattering; it doesn't yet.
- The `Insert template ▾` Select currently lives in the toolbar above the rows. Its position stays the same; it now appends to the AST and all three tabs reflect the change immediately.
- The existing `policy-templates.ts` shapes are correct; reuse them for both the Insert dropdown and the Worked-examples section's first 4 entries.
- Consider stable React keys on the rule list (existing code uses `useId()` + index — fine for now, but if reordering ever lands, switch to per-policy stable IDs).

---

## V2 amendment (2026-05-03 — landed after the v1 review)

The v1 plan shipped Tasks 1–6 as written. On review the user rejected the graphical Form tab as built:

- Operator-prefix layout (`eq` then operands) reads as `eq Thing Thing` instead of the natural infix `Thing eq Thing`.
- Operand-kind pickers (`Literal | User ref | Row ref | Expression`) expose raw choices instead of narrowing to what fits the slot. The right side of `in` should only allow array-typed user fields (`role_ids`, `role_names`) or a literal list, not arbitrary refs.
- Row-field text inputs have no autocomplete from the table's actual columns / JSONB schema.
- USER-namespace dropdown lists fields that don't make sense in every context (`is_platform_admin` is a boolean, not eq-comparable to every operand).

A real fix needs (a) the table's column schema piped into the editor, (b) infix operator layout, (c) per-slot type narrowing wired to the validator's known shape constraints. That's a redesign, not a polish pass. Out of scope for this branch.

### V2 decisions

1. **Drop the Form tab entirely.** The editor is two tabs: JSON and YAML. The reference panel + Insert Template toolbar are the teaching surfaces; users learn the AST by example and edit raw.
2. **Auto-seed empty buffers.** When `value === null`, the JSON tab shows `{"policies": []}` and the YAML tab shows `policies: []`, so the user has the wrapper ready to paste into without manual editing. Empty buffer (user clears the editor) still collapses to `onChange(null)`.
3. **Reference panel examples are wrapped.** Each EXAMPLES entry's `policy` field becomes a full `TablePolicies` object (`{policies: [<single Policy>]}`) so Copy → paste-into-fresh-JSON works directly without manual editing. The heading still mirrors the inner policy's name.
4. **Strict parser, no single-Policy fallback.** The JSON/YAML parser accepts only `{"policies": [...]}`. Anything else is a parse error. (Considered allowing bare Policy → wrap, but it's a footgun: silent acceptance hides a real shape mismatch.)
5. **Drop the client-side Monaco JSON Schema bind.** The bind we shipped in v1 was misbound — it registered the `Expr` schema as if the whole document should match, producing noise. Fixing it would mean duplicating the server's Pydantic validator in JSON Schema, which drifts. Instead:
6. **Server-side validation endpoint.** Add `POST /api/tables/policies/validate` that runs the existing `Expr` Pydantic validator and returns either `{"ok": true}` or `{"ok": false, "errors": [{"path": "$.policies[0].when.eq[1]", "message": "..."}, ...]}`. The Pydantic validator already prefixes each ValueError with the path; convert to the structured shape. This endpoint is the single source of truth for "is this policy valid"; the editor just calls it.
7. **Editor wires to the endpoint with debounce.** Whenever `jsonText` / `yamlText` parses successfully into a `TablePolicies`, debounce ~300ms then `POST /policies/validate`. Render returned errors inline beneath the existing parse-error display. Parse errors are syntax (Monaco's stock JSON parser); validation errors are semantic (server-returned). Both reuse the same display row, distinguished by source label. On save the server still validates authoritatively at the create/update endpoint; this is just a faster preview.
8. **Delete `policy-monaco-schema.ts` and the `onMount` schema-configure call** in `PolicyCodeView.tsx`. Monaco's stock JSON syntax checking is enough; semantic validation is the new endpoint.

### Files affected by the V2 amendment

**Delete (commit A):**
- `client/src/components/tables/PolicyFormView.tsx` (+ test)
- `client/src/components/tables/PolicyExpressionBuilder.tsx` (+ test)
- `client/src/components/tables/expr-shapes.ts` (+ test)
- `client/src/components/tables/policy-monaco-schema.ts`

**Modify (commit A):**
- `client/src/components/tables/PolicyEditor.tsx` — two-tab JSON/YAML editor; auto-seed `{"policies": []}` on null value; toolbar (Insert Template + Reference) preserved; no Add policy button.
- `client/src/components/tables/PolicyCodeView.tsx` — drop the schema-configure mount call.
- `client/src/components/tables/PolicyReferencePanel.tsx` — wrap each example's `policy` field as `{policies: [<inner>]}`.
- `client/src/components/tables/PolicyEditor.test.tsx` — remove all Form-tab assertions; add JSON-seeded-on-null test and JSON↔YAML round-trip.
- `client/src/components/tables/TableDialog.test.tsx` — re-route the un-skipped edit-mode round-trip through the JSON tab.
- `client/src/components/tables/PolicyReferencePanel.test.tsx` — assertions reflect the wrapped JSON.

**Add (commit B):**
- `api/src/handlers/tables_handlers.py` (or wherever the table router lives) — `POST /api/tables/policies/validate` endpoint accepting a `TablePolicies` body, returning `PolicyValidationResponse` (success or structured errors).
- `api/shared/models.py` — `PolicyValidationResponse`, `PolicyValidationError` Pydantic models.
- Server-side unit test in `api/tests/unit/test_policies.py` (or e2e if needed) covering the validate endpoint's success and error shapes.
- `client/src/services/tables.ts` (or similar) — `validatePolicies(body)` wrapper.
- `client/src/components/tables/PolicyEditor.tsx` — debounced validate-on-parse-success; render returned errors inline.

### Out of scope for V2

- Any graphical builder. Reintroducing one needs the table-column schema piped through and the contextual-operand redesign. File a follow-up plan.
- Validate endpoint authentication beyond what the `tables` router already requires (re-use the same auth dependency).

### Followups landed after the V2 amendment

- **`CodeEditor` generalized.** `PolicyCodeView` was renamed to `CodeEditor` (`client/src/components/tables/CodeEditor.tsx`) with `path` and `height` props so a single Monaco wrapper serves every consumer. `PolicyEditor` passes `policies.json` / `policies.yaml`; `TableDialog`'s schema field passes `table-schema.json`; `PolicyReferencePanel` passes `example-N.json` per example. Unique paths matter — Monaco's models are keyed by URI, and 16 read-only example editors plus the two policy tabs all coexist on the same screen when the dialog is open.
- **TableDialog's Schema field is Monaco.** Was `<Textarea>` with `font-mono`; now `<CodeEditor mode="json" path="table-schema.json" height="200px">`. Same `JSON.parse(values.schema)` submit-handler — Monaco emits a string just like the textarea did. Tests retargeted from `getByLabelText(/^schema/i)` to `getByLabelText("table-schema.json")`.
- **Reference panel examples are syntax-highlighted via read-only Monaco.** Each of the 16 examples renders through `<CodeEditor readOnly height="180px">` instead of plain `<pre><code>`, so every JSON snippet on screen has identical color and theming. The Copy button is unchanged — it formats via `JSON.stringify` independently of how the JSON renders.
- **Read-only Monaco hides the gutters.** When `readOnly` is true, `CodeEditor` sets `lineNumbers: "off"`, `folding: false`, `lineDecorationsWidth: 0`, and `lineNumbersMinChars: 0` to reclaim the left margin (line numbers and folding chrome don't help on display-only snippets). Editable consumers keep both gutters.

### Notes for future work

- **Real graphical builder.** When this lands, prerequisites include: (a) the table's column schema piped through `TableDialog` → `PolicyEditor` so the row-field slot offers autocomplete; (b) per-slot type narrowing wired to the validator's known shape constraints (e.g., `in` right side restricted to array-typed user fields like `role_ids`/`role_names` or a literal list); (c) infix `Thing eq Thing` layout, not the prefix `eq Thing Thing` form the v1 builder used.
- **Schema validation for the table Schema field.** The schema field accepts arbitrary JSON Schema. Monaco has no schema bound to it — typos and structural errors only surface at save time when the server validates. A `POST /api/tables/schema/validate` endpoint mirroring the policy validator would close that loop the same way `/policies/validate` did.
- **Reference panel weight.** With 16 read-only Monaco editors, the panel can feel heavy on first open. Lazy-mount each example's editor on first scroll-into-view (intersection observer) if performance matters; not needed today.
