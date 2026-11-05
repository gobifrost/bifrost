# Temporary secret access for local development

Platform Admins can explicitly assign themselves Secrets Reader through their
user page's Role Assignments section. This uses the ordinary role-assignment API
and its `roleassignments.readwrite` and `privilegedaccess.readwrite` checks.
Other users cannot edit their own role assignments.

Secrets Reader permits decrypted SDK secret reads everywhere: Global, the
provider organization, and all managed organizations. Platform Admin alone
does not grant `secrets.read`. The role is never granted automatically during
setup or promotion; its assignment is audited and can be removed through the
same role controls. The last active Platform Admin remains protected from
removal.

This is a temporary workaround for the current local-development workflow.
The intended development path keeps secret use inside the engine and does not
require a human Secrets Reader grant. That direction includes removing the
local executor. Self-assignment is not the long-term development architecture.
