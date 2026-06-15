"""One standard for org targeting across all CLI commands.

``--org <id|name|none|global>`` + a ``--global`` alias; omitting both => the
caller's home org. ``--organization`` and ``--scope`` are permanent synonyms for
``--org`` (added by :func:`org_option`), so the three spellings are
interchangeable on every org-targeting command.

The resolved :class:`OrgTarget` has THREE wire states that must stay distinct:

- **HOME** (``is_set=False``): send nothing — the server uses the caller's org.
- **GLOBAL** (``is_set=True``, ``organization_id=None``): explicit global (NULL org).
- **ORG** (``is_set=True``, ``organization_id=<uuid>``): that org.

HOME and GLOBAL are NOT the same: HOME omits the field (caller's org), GLOBAL
sends an explicit null. Conflating them is the footgun this standard removes.
"""

from __future__ import annotations

from dataclasses import dataclass

import click

_GLOBAL_SENTINELS = {"none", "global"}


@dataclass(frozen=True)
class OrgTarget:
    """A resolved org target (see module docstring for the three states)."""

    is_set: bool
    organization_id: str | None

    @staticmethod
    def home() -> "OrgTarget":
        return OrgTarget(is_set=False, organization_id=None)

    @staticmethod
    def global_() -> "OrgTarget":
        return OrgTarget(is_set=True, organization_id=None)

    @staticmethod
    def org(uuid: str) -> "OrgTarget":
        return OrgTarget(is_set=True, organization_id=uuid)


async def resolve_org_target(org: str | None, is_global: bool, resolver) -> OrgTarget:
    """Map ``(--org value, --global flag)`` to an :class:`OrgTarget`.

    ``none``/``global`` are reserved sentinels checked BEFORE org-name
    resolution, so an org literally named "none"/"global" resolves to global
    (those names are reserved).
    """
    if is_global and org is not None and org.lower() not in _GLOBAL_SENTINELS:
        raise ValueError("--org <org> and --global are mutually exclusive")
    if is_global:
        return OrgTarget.global_()
    if org is None:
        return OrgTarget.home()
    if org.lower() in _GLOBAL_SENTINELS:
        return OrgTarget.global_()
    uuid = await resolver.resolve("org", org)
    return OrgTarget.org(uuid)


def org_option(fn):
    """Add the standard ``--org`` + ``--global`` to a command.

    ``--organization`` and ``--scope`` are declared as permanent secondary names
    of the same ``--org`` option, so all three map to the ``org`` parameter.
    """
    fn = click.option(
        "--org",
        "--organization",
        "--scope",
        "org",
        default=None,
        help="Org UUID/name, or 'none'/'global' for global scope. "
        "Omit = your org. (--organization / --scope are synonyms.)",
    )(fn)
    fn = click.option(
        "--global",
        "is_global",
        is_flag=True,
        default=False,
        help="Target global scope (org=NULL). Alias for --org global.",
    )(fn)
    return fn
