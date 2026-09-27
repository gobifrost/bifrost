# Workspace and Dashboard Header Design

## Goal

Make navigation between the workspace catalog and dashboard feel like switching views in one stable page rather than moving between differently sized pages.

## Design

- Use `Workspace` as the catalog name everywhere in this header.
- For platform administrators, replace the visible page title with title-sized `Workspace` and `Dashboard` route tabs. The active tab is also exposed as the page's level-one heading.
- For users without dashboard access, render only a plain `Workspace` level-one heading and no dashboard navigation.
- Keep each page's existing description and primary action: `New chat` on Workspace and refresh on Dashboard.
- Use the Dashboard container width (`max-w-[1400px]`) for both routes so navigation does not resize the main workspace.
- Preserve responsive behavior and the protected dashboard route.

## Verification

- Component tests cover active routing, the `Workspace` label, heading semantics, and the non-admin fallback.
- The Home page test covers both permission variants and confirms the old standalone Dashboard action is gone.
- The existing Home Playwright journey switches between the two title tabs and confirms the shared 1400px container.

