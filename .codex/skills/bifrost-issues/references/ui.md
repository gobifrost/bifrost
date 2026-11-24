# Step 4 — UI

Apply [DESIGN.md](../../../../DESIGN.md) for tokens, page-specific behavior, and mobile layouts. Use these checks to review the affected surface.

In idea mode, apply only 4a: name the reference page and the components the build will
reuse, and flag any part of the proposal that would violate a rule below.

## 4a. Pick the reference before building

| Screen type | Reference to match |
|-------------|--------------------|
| Resource list / catalog | `/agents` (ResourceCatalogCard), Home |
| Resource detail | individual agent page |
| Data table / history / report | `/history` |
| Run / execution details | execution details page |
| Workspace (tree + list + inspector) | Files, Tables |
| Editor / builder | form builder |
| Settings | Settings workspace |

Also name the closest outside convention when one exists (GitHub Actions for test runs,
Azure audit tabs, Entra scope picker). Reuse shared components; if one is missing, build
it as a reusable component — never ship a degraded approximation.

## 4b. Rules

1. **Consistency** — "match the sibling" means match the reference page above, not
   whatever the nearest page does. Fix shared causes across affected siblings within scope.
   Match the sibling page's header, search, add button, table, row
   actions. Fix layout bugs in the shared component, not per page. Sibling surfaces
   (workflow vs agent vs form) get the same controls.
2. **Scrolling** — keep controls for a bounded list outside its scroller. Short lists fit
   their content. At the height limit, scroll the list and keep its actions accessible.
   Check short and long lists, long JSON, and each independently scrolling pane.
3. **Space** — compare the first visible rows or controls with the reference page at the
   same width. Remove redundant headings, card layers, and blank space that pushes the
   main task below the fold.
4. **Actions** — keep the primary action in the reference toolbar slot across tabs and
   changes in content. Follow the reference for row selection and overflow actions.
   Verify keyboard and touch access and check that the action fits at narrow widths.
5. **Presentation** — statuses/enums as colored Title Case badges with icons; entities as
   resolved names with links, never raw IDs; structured data in formatted viewers; errors
   as sentences; color is semantic.
6. **Symmetry** — fixed slots for status and date; aligned badges and compact comparison rows. Use the reference layout for records that need wrapping metadata.
7. **Tables** — Organization first; one column stretches; actions never hidden;
   truncate description first; no horizontal scroll at realistic data lengths.
8. **Pickers and inputs** — every org/workflow/role/user/model picker is the searchable
   select. Use the design-system component (date picker, select, combobox) over the
   browser-native control; if none exists, build it as a shared component.
9. **Feedback** — every action shows progress and result in place; live views load then
   stream; no full-page reloads; hide controls that do nothing.
10. **Navigation** — tabs, filters, selection in the URL; back restores place.
11. **Permissions** — hide actions the user cannot perform.
12. **Mobile** — every operational page usable at phone width; full-screen sheets have an exit.
13. **Copy** — Title Case for non-sentences; no redundant qualifiers; one term per concept.

## 4c. Prove it before showing anyone

1. Capture the affected flow with `./test.sh client e2e --screenshots <spec>`. Check desktop and phone widths (1440×900 and 390×844 are starting points), light and dark, and any intermediate width where the layout changes.
2. Read every screenshot. Check against the reference page and rules 1–13.
3. Fix any failure and repeat the affected check. Record the rendered evidence and any
   states you could not verify.
