# Zero Trust Automation

Zero Trust Automation is Bifrost's name for the security model that governs
roles, organization reach, run identities, and workflow permissions.
Authenticated automation checks platform operations against the run user's
current authority.

## Roles, Reach, and Identity

Roles grant named permissions. Their organization or platform boundaries
determine the run user's reach. Workflow permissions can add powers only within
that reach.

A run records its original user and acting identity separately. Run As changes
who performs an action and receives its attribution. The original run user's
current roles and reach continue to decide whether the action is allowed.
Unattended runs require an identity selected for the workflow, Agent, or Service.

## Workflow Access Modes

The Workflow Access panel shows the effective mode:

| Mode | Permissions added to the run user's own roles |
| --- | --- |
| **Full Access** | Every named permission, including Secrets, within the run user's organization reach. |
| **Declared Permissions** | Only the workflow's configured permissions, within the same reach. |

Table and file policies still apply. Role conditions use the original run
user's own role membership. Agents and inline scripts add no workflow powers.
Solution outbound-access settings separately control network access.

Full Access remains the platform default. This upgrade does not convert
workflows to Declared Permissions.

## Solution Permission Requests

A Solution declares each workflow's requested mode and permission capabilities.
An administrator reviews and approves the request for that installation. Changed
requests require approval again. New explicit requests start with no added
capabilities until approved. Declared Permissions requests cannot inherit Full
Access from the platform default. Runtime approval is bound to the current
installation and request digest; revoked or stale approvals add no capabilities.
Removing a request keeps its existing restriction. Existing Full Access installs
retain their mode until explicitly restricted.

The API and manifest values remain `full` and `restricted`; `restricted` is the
stored value for Declared Permissions. For example, a Solution workflow can
request:

```yaml
requested_permissions:
  mode: restricted
  grants:
    - permission: tables.read
```

These permission capabilities add no organizations to the run user's reach.

## Moving to Declared Permissions

Use workflow operation usage and code dependencies to identify the permissions
and organization boundaries a workflow needs. Access Checks records refusals
and selected policy and secret decisions, but it omits successful named
permission checks. It is evidence, not a complete usage analysis.

Approve a narrower Solution permission request, then verify the workflow's
expected paths before converting the next group. Keep Full Access where its
broader permissions are still required.
