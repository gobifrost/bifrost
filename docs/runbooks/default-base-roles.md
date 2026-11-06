# Chat Access and Default Base Roles

Every person and custom identity has one base role. A custom base role replaces
User's permissions; additional roles add their own grants at their assigned
boundaries.

## Create a base role without Chat

1. Create a custom role, such as **User Without Chat**.
2. Open its Permissions panel and choose **Use User Permissions**.
3. Clear **Use Chat** and save.
4. Assign that role in an account's **Base Role** field, or select it as a
   provisioning default.

The built-in User role includes Use Chat by default, including on upgrades.
Existing custom roles need an explicit Use Chat grant to allow Chat. Removing
Use Chat hides the Chat entry points and denies Chat routes, API requests, and
live conversation subscriptions. Conversations remain stored.

Use Chat is separate from Use AI (`ai.execute`) and Run Agents
(`agents.execute`). Changing Chat access does not change SDK AI or MCP execution
permissions. Additional roles can grant Use Chat back; a base role is not a deny
policy, and Platform Admin retains access through its wildcard.

## Choose the default for new accounts

Open **Settings → Base Role Defaults**. Select Global for the instance default,
or an organization for an override. An organization can inherit Global again.

Provisioning chooses the base role in this order:

1. An explicit base role supplied when creating the account.
2. The organization's configured default.
3. The instance's configured default.
4. The built-in User role when the instance default has not been configured.

The same resolution applies to invited users, domain/SSO provisioning, and new
custom identities. Existing accounts retain their assigned base roles when a
default changes or an account moves to another organization.
Editing a role's permissions still changes access for every account holding
that role.

A default must be User or a custom role without privileged access. A role that
is configured as a default cannot be deleted or changed to grant privileged
access until its default selections are removed.

The protected Global and organization Default Identity accounts retain their
fixed User base role. Create a custom identity with the required base role and
select it on the workflows that should use it.

CLI equivalents:

```bash
bifrost users defaults get
bifrost users defaults set --role "User Without Chat"
bifrost users defaults set --org Contoso --role "Portal User"
bifrost users defaults set --org Contoso --inherit
bifrost users defaults set --inherit
bifrost users create --identity --org Contoso --name "Nightly" --base-role "Automation"
```

## Update existing accounts

Select the intended accounts in Users and choose the bulk base-role action.
Review the selected accounts and the new role before applying. The operation
retains additional roles and reports each account it could not change. It uses
the same lifecycle, role-assignment, and privileged-access checks as individual
assignment. Protected default identities and self-assignment are refused.

These controls do not implement spending budgets or an unconditional
instance-wide Chat off-switch. Automated work is governed by the identity that
runs it; person-initiated work uses that person's authority.
