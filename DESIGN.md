# Bifrost product design contract

Canonical authority: `gobifrost/design-system` at `11da72e`. The user approved bringing its existing identity into the complete platform, with custom-brand support and preservation of behavior. This is an operational product; hierarchy and reliable interaction carry the character.

## Foundations

Use Inter for interface text, Prompt for restrained identity/display, and JetBrains Mono for code and measurements. Default Bifrost dark canvas is #08090b, surface #0a0c0f, primary #2fd4d4; light canvas #f7f9fa, surface #ffffff, primary #087f86. Semantic colors retain independent success/warning/error meaning. Follow canonical tokens for 6px controls, 4px surfaces, 8px feature radii and compact/comfortable/spacious density. Elevation belongs to floating layers; tonal surfaces and hairlines establish ordinary hierarchy.

## Branding

Existing runtime branding remains authoritative. Derive action/hover/focus/contrasting foreground and activity-gradient roles from the selected custom brand. Default identity uses the Bifrost spectrum. Custom brands use coherent tonal or hue-related stops instead of inheriting an unrelated rainbow. Validate light/dark contrast. Do not recolor semantic errors or successes into brand accents.

## Motion

Feedback 120ms, disclosure 220ms, route at most 360ms, genuine activity 1600ms. Preserve reading position during streams. Spectrum/brand-gradient motion conveys actual route or execution activity, never invented completion percentages. Connection state is distinct from execution outcome. Reduced motion removes loops, translation, blur and clipping while retaining legible state.

## Composition and compatibility

Shared header/action/filter/table/inspector patterns provide consistency, with each page designed for its actual task. Avoid proliferating cards and competing primary actions. Preserve route terminology and navigation expectations. On mobile use focused panes, deliberate disclosure, accessible controls and appropriate table alternatives; do not shrink a desktop workspace wholesale.

Retain existing Radix-backed component APIs and V1 runtime exports. Source-copy registry components are visual references, not automatic replacements for richer product contracts. Preserve portaled overlays, keyboard semantics, refs and compound components. Keep customer-authored content outside blanket page-layout selectors.

## Copy

Anything that isn't a sentence is Title Case: buttons, headings, tabs, labels, badges, menu items, column headers, and display names ("Save Roles", "Effective Access", "All Customer Organizations"). Short words stay lower case inside a name ("Sign Out of All Devices", "Takes Effect with R3b"). Sentences keep sentence case: descriptions, help text, errors, toasts, tooltips, and empty-state messages that read as a sentence.

A heading names the thing; what it shows or why belongs in its description ("Effective Access", not "What they can do, and where").

Permissions are named the way Microsoft Graph names them: `{Verb} {Resource}`, from the permission catalog's `names`. Verbs are Read, Read Basic, Read and Write, Read All and Read and Write All, or a resource's own verb (Run Workflows, Run Agents, Use AI, Impersonate Users, Publish Apps, Deploy Solutions, Build Solutions). The Platform Admin wildcard is All Permissions. Show these names wherever a permission appears, in the UI and in CLI output; never the raw `domain.action` string as the label.

A permission string has the Graph shape `resource.action[.all]`: all lower case, and the resource is one word with no dot. Read Basic is the everyday view of items shared with the holder; Read is the full view. `.all` means other people's private items (agents, runs, artifacts) and never widens which organizations the holder reaches. No permission implies another: Read and Write does not include Read, and Read All does not include Read.

## Verification

Every coverage-ledger item requires an individual source review and applicable rendered evidence. Test themes, branding, widths, states and important interactions. A build or token replacement is not proof that a page has been redesigned.


## Tables

Organization is always the first column when a table has one, and the first labeled value in its phone record summary.


## Mobile data presentation

Mobile acceptance means an easy-to-use, readable composition, beyond preventing page overflow. Review the user's task at the narrow viewport: show record identity and the highest-priority value together, retain the remaining labeled values, and make sorting and actions directly usable. Prefer record summaries for dense report/list tables where sideways reading separates labels from values. Keep desktop comparison tables when useful; both representations must share data, ordering, permissions and actions. Test the intermediate widths where the sidebar reduces available content width. A horizontal scroller alone is not evidence that a dense data page is mobile-friendly.

Selected control groups use tenant primary tokens. Semantic green indicates a meaningful positive status or outcome; audit hard-coded green selection treatments independently from success indicators.


## Home, collections, and execution

Home combines accessible apps, forms, and chat-enabled agents, with personal pins and collections plus administrator-curated shared collections. Organization and resource type must remain visible when browsing across customers. Collections organize existing access; they do not grant it. Use the existing Lucide library for collection icons.

Execution is result-first. The full page and preview share Result, Input, Logs in that order, defaulting to Result. Logs are optional supporting detail, with wrapping messages and secondary timestamps; never reserve a blank log column. Never infer workflow steps or percent completion from plain logs. Use an indeterminate brand-aware activity trace while running, respect reduced motion, and preserve manual inspector choices when results arrive. On desktop, selected content scrolls within the available height; on mobile, use full-width sections. History preview must preserve table scroll bounds and keep shared pagination visible.

See [Home and execution experience](docs/design-modernization/home-execution.md) for behavior, verification, and rollout details.

### Execution section headings and log severity

Agent run Overview and Activity share a centered reading width. Overview preserves
the asked/done/answered narrative in one surface. AI Usage is a compact footer
disclosure with summary metrics; expanded usage and run metadata use the full
surface width rather than a separate sidebar. Activity sits below Overview in a
contained workspace with its own header, bounded desktop scrolling, and an attached
animated call inspector. Focus activity temporarily prioritizes that workspace;
Show overview restores the narrative. Mobile inspection uses a full-width pane.
While browsing, short Activity lists fit their content. Selecting a call expands
the workspace within the available page pane and reveals its header, preserving
Overview above. Closing shrinks the list and restores focus. Highlight backgrounds
meet the header and inspector divider without an inset gutter. Reduced motion
must still reveal the header after the new height is applied.
Nested activity uses branch connectors with an elbow for each child and a stem
that stops at the last sibling. Activity and Entity Management use full-width
row selection, from the container edge to the inspector divider, regardless of
depth. Indent contents rather than selection surfaces. Use `tree-row-selected`
for the shared opaque selection tint so ancestor guides cannot show through it.
Selection persists on hover; pointer feedback must not replace selection feedback.

Entity Management uses a contained resource directory with expandable relationships,
not table columns. Resource identity leads; scope, type, and access remain visible
as wrapping metadata. Selection and bulk editing belong to the same workspace.
Keep managed-resource restrictions, explicit change modes, and partial-failure
feedback intact when changing the presentation.

Run Activity, Execution Context, Runtime Variables, and Usage use the shared ExecutionSectionHeading: title case, foreground display text, a primary-colored Lucide icon, and an optional muted description. Uppercase metadata labels are not section headings.

Readable activity and detailed logs share LogEntryRow severity framing. A 3px leading stripe uses semantic info, warning, danger, or muted-debug tokens, independent of custom branding. Preserve text/icon severity cues as well as color. The newest-message highlight indicates recency and must not override severity. Keep filtering, traceback grouping, copying, and follow-scroll behavior in the detailed log panel.

Files is a contained directory workspace: shares and folder branches on the left,
a searchable file directory in the center, and an animated, attached inspector
for preview and access. Directory selection uses the same full-width
`tree-row-selected` surface as Activity and Entity Management. Only row content
is indented. File actions live in the standard overflow menu; opening that menu
must not activate the row. Folders use warm icons and shares use the brand accent.
Keep scope in the workspace header, breadcrumbs above the directory, and policy
search inside Access Policies. Do not repeat the policy heading in another toolbar.
At intermediate widths, hide share navigation while inspecting; on phones the
inspector replaces the directory inside the workspace, never the whole screen.
Keep directory search and item counts outside the scrolling rows. Preserve explicit
Global/organization/solution scope, read-only locations, and mutation recovery.

The Files scope field and navigation share one column width; Shares and the
breadcrumb toolbar share a desktop row height. Directory navigation preserves
Files/Access Policies mode. Policies lists explicit attachments under the selected
folder; inherited access belongs in the inspector. Policy summaries explain their
source and resolve named rules, while user/action decisions require Test Access.
Edit policy attachments and test access inside the inspector, with readable rules
first and a lossless Advanced editor for custom conditions. Keep mutation guards,
source/blast-radius context, and a return path to the same Access tab.

Knowledge uses the same contained workspace grammar for document operations.
The toolbar owns search plus namespace and scope filtering. Rows lead with the
document title and preview, with namespace and organization scope wrapping under
the title as metadata instead of occupying narrow columns. Bulk actions require
an explicit selection mode. Opening a document attaches the embedded editor to
the workspace as a contained pane on desktop and as the workspace pane on mobile,
without a modal or portal. Preserve loading, save, conflict replacement, scope,
permission, and mutation guards while the editor is attached.

Tables follows the same workspace grammar. Catalog selection is an explicit
Select switch, with compact title/Markdown preview/scope/date rows. Scope and
search are flush adjoining toolbar regions; refresh stays an icon. Compare
scalar record fields in a bounded desktop grid and use summaries on narrow
screens. Open formatted data and complete JSON in the attached inspector, never
in expanding table rows. Table settings and record editing keep fixed footer
actions within that workspace. IDs use HoverCopyText, with a copy hint above
the text on hover/focus and direct keyboard/touch copying. See
docs/design-modernization/tables-workspace.md for the preserved behaviors.

Contained workspace toolbars use WorkspacePrimaryAction for the trailing
Add/New/Upload action: flush with the right edge, square inner corners, full
row height, and an inset focus ring. Keep secondary controls padded and quiet.
On narrow screens the action stays at the right edge of its wrapped row.
Tables keeps its route/deep links while retaining the Data Tables page header;
the selected table name and return link live in a workspace breadcrumb.

WorkspaceHeader gives directory tabs and detail breadcrumbs the same 48px
navigation band, background, and divider. Descriptions must not add height to
this row; table descriptions use an accessible About This Table popover with
MarkdownContent. Preserve the frame position when navigating into a table.

Settings is a contained workspace: navigation and settings content share one
outer frame and divider, with a tinted navigation surface and a persistent
brand-colored active item. The frame owns page width; individual settings
panels should not impose narrower max-widths. Desktop navigation and content
scroll independently; mobile navigation collapses into the frame header.

Home is the visual reference for resource catalog cards. Home, Apps, Forms,
Agents, and Workflows use ResourceCatalogCard: resource icon at the top left,
pin or management action at the top right, shared title/type/description
placement, and organization at the bottom left. The primary target stretches
over the card; secondary controls remain separate accessible targets. Cards
open the resource or its execution controls, never start a workflow merely
by selecting the card. Agent management cards retain their details destination;
Home agent cards retain Chat. Form editor back navigation belongs beside the
title on the left, preserving the originating Solution when applicable.

Primary and Settings navigation share navigationSelectionClasses: square
selection rows, a straight leading border, and the same subtle brand tint.
Do not substitute rounded inset-shadow accents in workspace navigation.
