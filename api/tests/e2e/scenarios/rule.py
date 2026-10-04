"""The execution-identity rule and the starts it is checked against.

The rule (managed-identity model): an attempt to act in a target org is
allowed iff the target is in the run user's reach (Global is in everyone's). The run user is the person
who called the run; with no caller it is the managed identity (MI) of the
workflow's org. Child runs and ``run_as`` keep the original user's reach. The
workflow adds powers (every probe here is Full), never reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TARGETS = ("contoso", "fabrikam", "provider", "global")
EVERYWHERE = frozenset(TARGETS)

# Reach of each run user: home, every org a role of theirs is placed on, and
# Global, which is in everyone's reach. "staff" are provider-org members, who
# become Operators at every customer org with the Operator migration. The
# global identity has no home, so it reaches Global only.
REACH: dict[str, frozenset[str]] = {
    "admin": EVERYWHERE,
    "staff": EVERYWHERE,
    "customer": frozenset({"contoso", "global"}),
    "hr": frozenset({"contoso", "global"}),
    "external": frozenset({"contoso", "global"}),
    "custom_base": frozenset({"contoso", "global"}),
    "fabrikam_customer": frozenset({"fabrikam", "global"}),
    "mi:provider": EVERYWHERE,
    "mi:contoso": frozenset({"contoso", "global"}),
    "mi:global": frozenset({"global"}),
}
PLATFORM_ADMINS = frozenset({"admin", "mi:provider"})

# The MI an unattended run of each probe uses. "global" is the shape of an
# existing global workflow with unattended triggers, which the migration gives
# the platform org MI; "global_new" keeps the global MI.
PROBE_MI = {
    "provider": "mi:provider",
    "contoso": "mi:contoso",
    "global": "mi:provider",
    "global_new": "mi:global",
}


@dataclass(frozen=True)
class Child:
    """A child run spawned by a probe via ``workflows.execute``."""

    org: str | None = None
    run_as: str | None = None
    children: tuple[Child, ...] = ()

    @property
    def key(self) -> str:
        return f"child[{self.org or '-'},{self.run_as or '-'}]"


@dataclass(frozen=True)
class Start:
    """One way a run begins: entry point, who (if anyone) calls it, which probe."""

    entry: str
    probe: str
    person: str | None = None
    request_org: str | None = None
    children: tuple[Child, ...] = field(default=())
    source: str | None = (
        None  # home of the event source, when it differs from the probe's
    )

    @property
    def key(self) -> str:
        who = self.person or "nobody"
        entry = f"{self.entry}@{self.source}" if self.source else self.entry
        suffix = f"->{self.request_org}" if self.request_org else ""
        return f"{entry}.{who}.{self.probe}{suffix}"

    @property
    def user(self) -> str:
        return self.person or PROBE_MI[self.probe]


STARTS: tuple[Start, ...] = (
    # A person starts a probe directly (REST, also what the web SDK and CLI use).
    Start(
        "rest",
        "global",
        "customer",
        children=(
            Child("contoso", "customer"),
            Child(None),
            Child("fabrikam"),
        ),
    ),
    Start("rest", "contoso", "customer"),
    Start("rest", "global", "hr"),
    Start("rest", "global", "external"),
    Start("rest", "global", "custom_base"),
    Start("rest", "global", "fabrikam_customer"),
    Start("rest", "global", "staff", children=(Child("contoso"),)),
    Start("rest", "provider", "staff"),
    Start(
        "rest",
        "global",
        "admin",
        children=(
            Child("fabrikam"),
            Child(None, "customer"),
            Child("contoso", "admin", children=(Child("fabrikam"),)),
        ),
    ),
    Start("rest", "contoso", "admin"),
    Start("rest", "provider", "admin"),
    # The request-level org override (web SDK useWorkflow sends it).
    Start("org_override", "global", "customer", request_org="contoso"),
    Start("org_override", "global", "customer", request_org="fabrikam"),
    Start("org_override", "global", "staff", request_org="contoso"),
    Start("org_override", "global", "admin", request_org="fabrikam"),
    # Other ways a person calls a run.
    Start("form", "global", "customer"),
    Start("chat", "global_tool", "customer"),
    Start("delayed", "global", "customer"),
    # Nobody calls the run: it runs as the MI of the workflow's org.
    Start("webhook", "provider"),
    Start("webhook", "global"),
    Start("webhook", "contoso"),
    Start("webhook", "global_new", source="contoso"),
    Start("schedule", "global"),
    Start("topic", "global_new"),
    Start("endpoint", "global"),
    Start("endpoint", "contoso"),
    # Direct table reads with the person's own token (web SDK useTable scope).
    Start("direct", "global", "customer"),
    Start("direct", "global", "staff"),
    Start("direct", "global", "admin"),
)


def expected_allowed(user: str, target: str) -> bool:
    return target in REACH[user]


def expected_spawn(user: str, child: Child) -> bool:
    """A child may be started in an org only inside the original user's reach."""
    return child.org is None or child.org in REACH[user]


def expected_identity(user: str, run_as: str | None) -> tuple[str, bool]:
    """(identity label, is_platform_admin) a run should report.

    A run reports its own user: ``run_as`` changes who that is, never the
    reach, and a child without ``run_as`` keeps the original user.
    """
    who = run_as or user
    label = who if who.startswith("mi:") else f"person:{who}"
    return label, who in PLATFORM_ADMINS
