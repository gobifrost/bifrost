# Searchable Dropdown Behavior Design

## Problem

Searchable dropdowns built from the shared `Command` primitives inherit cmdk's fuzzy ranking and selected-item scrolling. With a long list, changing the query can scroll to a stale active item instead of presenting the current results from the top. The shared list also hides its scrollbar, so users cannot see their position or drag back to the beginning.

## Search contract

The shared `Command` component will use deterministic, case-insensitive token matching unless a consumer supplies its own `filter`. Every whitespace-delimited query token must appear somewhere in the item's declared search corpus: its stable `value` plus its `keywords`.

Consumers remain responsible for declaring meaningful searchable metadata. Entity pickers should include the visible label, description, identifier, and aliases in `keywords`. The generic `Combobox` will include its option label and description. `WorkflowSelector` already declares workflow name and description as keywords and uses the workflow ID as its value.

Search filters the entire mounted list. It does not limit matches to the currently visible or previously scrolled portion.

## Scrolling contract

Whenever a command query changes, the shared input will return its associated command list to `scrollTop = 0` after cmdk has applied filtering and selection updates. This prevents cmdk's stale selected-item scroll from leaving the user in the middle or at the bottom of the result set.

The existing item-level `scrollIntoView` containment remains in place. It is required to keep keyboard selection from scrolling an embedding host page across an iframe boundary.

Command lists will show a native vertical scrollbar when their content exceeds the height cap. The existing wheel containment behavior remains unchanged.

## Compatibility

Consumers that provide an explicit `filter` retain that behavior. Consumers that use `shouldFilter={false}` remain responsible for their own filtering. Selection callbacks, keyboard navigation, item ordering when the query is empty, and popover lifecycle are unchanged.

## Verification

Component tests will prove:

- default search matches values and keywords using case-insensitive multi-term inclusion;
- explicit custom filters still take precedence;
- changing a query resets a previously scrolled list to the top;
- list styling exposes a vertical scrollbar;
- generic combobox descriptions are searchable;
- workflow names and descriptions are searchable through the shared behavior;
- the existing cross-iframe selected-item containment test remains green.

The seeded debug preview will contain enough workflows, including SharePoint terms in names and descriptions, to make the list overflow and exercise both search and scrolling on the History page.
