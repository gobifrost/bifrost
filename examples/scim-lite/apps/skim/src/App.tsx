import { useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import {
  RequireRole,
  useOrganizations,
  useUser,
  useWorkflowMutation,
  useWorkflowQuery,
} from "bifrost";

// Skim: one screen per org for SCIM onboarding. Secrets stay server-side:
// the save workflow writes the mapping + group config, the browser only
// ever sees presence flags (api_token_set), never values.

type Status = {
  org_id: string;
  mapped: boolean;
  entity_id: string | null;
  base_url: string | null;
  dialect: string | null;
  domain: string | null;
  api_token_set: boolean;
  group_roles: Record<string, string | { role: string; orgs: string[] }>;
  roles: string[];
};

function Onboarding() {
  const { data: orgs } = useOrganizations();
  const [orgId, setOrgId] = useState("");
  const status = useWorkflowQuery<Status>(
    "functions/scim_admin.py::scim_onboarding_status",
    orgId ? { org_id: orgId } : undefined,
  );
  const save = useWorkflowMutation<{ org_id: string; mapping_id: string }>(
    "functions/scim_admin.py::scim_onboarding_save",
  );

  const [entityId, setEntityId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [dialect, setDialect] = useState("scim-lite");
  const [domain, setDomain] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [groupText, setGroupText] = useState("{}");
  const [groupError, setGroupError] = useState<string | null>(null);

  const current: Status | undefined = status.data;
  const items = (orgs as { items?: { id: string; name: string }[] } | undefined)?.items ?? [];

  function loadCurrent() {
    if (!current) return;
    setEntityId(current.entity_id ?? "");
    setBaseUrl(current.base_url ?? "");
    setDialect(current.dialect ?? "scim-lite");
    setDomain(current.domain ?? "");
    setApiToken("");
    setGroupText(JSON.stringify(current.group_roles ?? {}, null, 2));
    setGroupError(null);
  }

  function onSave() {
    let groupRoles: Record<string, unknown>;
    try {
      groupRoles = JSON.parse(groupText || "{}");
      setGroupError(null);
    } catch {
      setGroupError("Group map is not valid JSON.");
      return;
    }
    save.mutate({
      org_id: orgId,
      entity_id: entityId,
      base_url: baseUrl,
      dialect,
      domain: domain || undefined,
      api_token: apiToken || undefined,
      group_roles: groupRoles,
    });
  }

  return (
    <section className="mx-auto w-full max-w-3xl space-y-4 p-4 md:p-6">
      <h1 className="text-lg font-semibold">Skim — SCIM onboarding</h1>

      <label className="block space-y-1 text-sm">
        <span className="font-medium">Organization</span>
        <select
          className="w-full rounded-md border bg-background px-2 py-1.5"
          value={orgId}
          onChange={(e) => setOrgId(e.target.value)}
        >
          <option value="">Select…</option>
          {items.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </label>

      {current && (
        <div className="rounded-md border p-3 text-sm">
          <p>
            {current.mapped ? (
              <>
                Mapped to <code>{current.entity_id}</code> via{" "}
                <code>{current.base_url}</code>
                {current.api_token_set ? " (token set)" : " (no token)"}
              </>
            ) : (
              "Not mapped yet."
            )}
          </p>
          <button
            type="button"
            className="mt-2 text-sm text-primary hover:underline"
            onClick={loadCurrent}
          >
            Load current values into the form
          </button>
        </div>
      )}

      <div className="grid gap-3">
        <label className="block space-y-1 text-sm">
          <span className="font-medium">Tenant / entity ID</span>
          <input
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
            placeholder="tenant-a"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">IdP base URL</span>
          <input
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://idp.example.com/scim/v2"
          />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Dialect</span>
            <select
              className="w-full rounded-md border bg-background px-2 py-1.5"
              value={dialect}
              onChange={(e) => setDialect(e.target.value)}
            >
              <option value="scim-lite">scim-lite</option>
              <option value="keycloak">keycloak</option>
            </select>
          </label>
          <label className="block space-y-1 text-sm">
            <span className="font-medium">Email domain (optional)</span>
            <input
              className="w-full rounded-md border bg-background px-2 py-1.5"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="tenant-a.example.com"
            />
          </label>
        </div>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">API token (optional, stored as secret)</span>
          <input
            type="password"
            className="w-full rounded-md border bg-background px-2 py-1.5"
            value={apiToken}
            onChange={(e) => setApiToken(e.target.value)}
            placeholder={current?.api_token_set ? "•••••• (set — leave blank to keep)" : "paste token"}
            autoComplete="off"
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span className="font-medium">
            Group → role map (JSON; values are role names or {"{role, orgs}"} objects)
          </span>
          <textarea
            className="w-full rounded-md border bg-background px-2 py-1.5 font-mono text-xs"
            rows={6}
            value={groupText}
            onChange={(e) => setGroupText(e.target.value)}
          />
        </label>
        {current && current.roles.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Available roles: {current.roles.join(", ")}
          </p>
        )}
      </div>

      {groupError && (
        <p role="alert" className="text-sm text-destructive">
          {groupError}
        </p>
      )}
      {save.error && (
        <p role="alert" className="text-sm text-destructive">
          {save.error.message}
        </p>
      )}
      {save.data && (
        <p role="status" className="text-sm text-green-600">
          Saved.
        </p>
      )}
      <button
        type="button"
        className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        disabled={!orgId || save.loading}
        onClick={onSave}
      >
        {save.loading ? "Saving…" : "Save onboarding"}
      </button>
    </section>
  );
}

export default function App() {
  const user = useUser();
  if (user.isLoading) return <p className="p-4 text-sm">Loading…</p>;
  return (
    <Routes>
      <Route
        index
        element={
          <RequireRole
            role="Platform Admin"
            fallback={
              <p className="p-4 text-sm">
                Skim is for platform admins. <Link to="/">Back</Link>
              </p>
            }
          >
            <Onboarding />
          </RequireRole>
        }
      />
      <Route path="*" element={<p className="p-4 text-sm">Not found.</p>} />
    </Routes>
  );
}
