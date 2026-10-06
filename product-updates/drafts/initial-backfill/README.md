# Initial Backfill Review

This is draft product content, prepared from a frozen and verified landed range. It is not a published release and is excluded from approved production bundles.

Coverage: `v1.4.1` (`ade714a08b95c84daa285e859ebece55f45c6754`) through `32aeea16c99c27d1278b5576f09080e29a59cca2`: 124 commits, 124 verified PRs, no direct or unresolved commits. Subject-extracted PR numbers and GitHub commit associations reconcile exactly. Every source has a durable disposition in `product-updates/dispositions.json`: 68 PRs grouped into 16 highlights, 39 Other changes, 17 omissions with reasons.

A seventeenth staged entry describes this branch's app release notes and Discord announcement. It has a permanent UUID but no invented PR number or landed-source claim. It appears only with the explicit staged preview option and is not part of the frozen coverage interval.

Dates are this preparation/publication cycle, not invented retrospective announcement dates. All backfill entries currently say `review.status: draft`; Jack owns approval of the final words. Move approved entries into the normal entry directory and update their matching dispositions only after that review. A normal release requires approved classifications and security material; a preview flag is not approval.

## Review Items

- Confirm highlight selection, concise Other titles, and omission reasons.
- Confirm external credit for wilhil (#807), sdc53 (#801), and MTG-Thomas (#756, #764, plus preserved original #744 work in landed #745).
- Review run/event retention defaults and irreversible detailed-history cleanup before upgrade.
- Review secrets/Graph payload changes, MCP renames, admin management boundaries, and removed interactive CLI/session fields.
- Check the minimum CLI/SDK floor against the version that actually ships these changes.
- Review dependency advisories and applicability; upstream evidence is cached in `product-updates/evidence/security-review.json`. A server-only advisory in browser package release notes does not prove a Bifrost server exposure.
- Keep report-only workflow authorization separate from already-enforced identity/data restrictions.
- Approve the supplied community invite `https://discord.gg/f7TCcWX2s` for release prose; it was explicitly provided for the UI by Jack. Verify Never/No Limit in the separate Discord setup chat before relying on permanence.

## Assets

The Roles screenshot is authored once under `product-updates/assets/` and was captured from the isolated seeded debug build. Generated client copies are build outputs. GitHub preview URLs must use the commit that actually contains the asset; the old frozen application target does not contain newly authored announcement assets.
