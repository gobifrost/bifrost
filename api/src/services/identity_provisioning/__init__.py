"""Identity provisioning: keep Bifrost users and role assignments in step
with an external directory.

``core`` is protocol-independent. ``scim`` translates SCIM 2.0 requests into
core commands; a Google Directory sync feeds the same core with snapshots.
See ``docs/plans/2026-11-16-scim-identity-providers.md``.
"""
