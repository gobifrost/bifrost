# OAuth Connection Health Design

## Goal

Replace raw OAuth connection counts on integration cards with a compact aggregate health state and an inspectable status breakdown.

## Status model

The connection set contains the integration's current default OAuth token plus each distinct token explicitly attached to an organization mapping as an override. Inherited use of the default token does not create additional connection rows.

- **Connected:** at least one connection exists and every connection is successful.
- **Degraded:** successful and failed connections both exist.
- **Failed:** at least one connection exists, every durable connection state is failed, and none is successful.
- **None:** no successful or failed connection exists.

`completed` and legacy `connected` token values are successful. `failed` is failed. Authorization/testing states are shown in the breakdown but do not manufacture a durable healthy or failed aggregate.

## Interface

Each integration card or table row shows one compact, clickable badge. Clicking it opens a popover titled “OAuth connections” with counts for Connected, Failed, and Other states. Zero-count rows remain visible so the meaning of the aggregate is explicit. Integrations without OAuth configuration continue to show “Not monitored.”

## Data flow

`GET /api/integrations` retains the existing response fields. `mapping_count` continues to count mappings. `connection_status_counts`, `connected_count`, and `needs_reconnection_count` change to count distinct concrete OAuth connections: the latest integration-level token and mapping override tokens. This fixes the current omission of a healthy default connection without inflating it once per inheriting organization.

## Verification

- Backend coverage proves default tokens and distinct overrides are aggregated together.
- Component coverage proves all four aggregate labels and the clickable breakdown.
- One Playwright happy path exercises the list status popover and produces review screenshots.

