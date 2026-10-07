# Product Updates Runtime Contract

Every API/client image pair, including `:dev`, candidates and stable, carries the approved updates supported by that build. `scripts.prepare_product_updates_image` validates the cumulative content ledger before Docker packaging, writes the immutable API bundle and copies screenshots into the client public directory. Screenshot URLs include their content hash. No GitHub request, LLM, mutable entry table, seed transaction, background job, or polling loop is involved at runtime.

## Feed and Authorization

`GET /api/product-updates` requires Platform Admin and returns the running bundle plus the authenticated admin's seen entry UUIDs. Database receipts never widen the running image's visibility. A rollback serves the older bundle's content; newer receipts remain stored and become applicable again after upgrading. Production images consume approved canonical entries only. Draft and staged entries are not included.

## Automatic Acknowledgement

`POST /api/product-updates/receipts` receives the displayed UUID list. The server derives the admin from authentication, rejects unknown or release-only entry IDs, and upserts `(admin_id, entry_uuid)` idempotently. Callers cannot write another admin's receipts. Presentation timestamps mean the entry was displayed, not that somebody read or understood it.

The admin shell fetches the feed once on entry and opens the modal for unseen visible UUIDs. Rendering the modal or history automatically acknowledges that displayed batch. There is no Mark Read task. Direct history visits are unobstructed; history remains accessible through Help. Receipt failures are shown honestly and do not pretend persistence succeeded. Tabs communicate presentation through BroadcastChannel without sharing admin identity or introducing polling.

## Release Intervals

The source inventory remains cumulative so image history does not lose earlier notes after a full release. GitHub release preparation takes an explicit previous-final commit and target commit, filters that interval, and freezes a reviewed Markdown body. Publication consumes it. Entry revisions and dev-to-stable promotion preserve UUIDs and receipts; a new capability receives a new UUID.

## Separate Publication

Preparing or merging release data does not cut a tag, publish a GitHub release, post to Discord, or change repository rulesets. A future Discord publication consumer needs its own durable publication identity and destination receipt. Live publication remains separately authorized.
