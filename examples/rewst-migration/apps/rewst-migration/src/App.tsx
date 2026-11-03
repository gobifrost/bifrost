import {
  useEffect,
  useMemo,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import {
  Link,
  NavLink,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useOutletContext,
} from "react-router-dom";
import {
  ArrowRight,
  CircleAlert,
  Clipboard,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import {
  BifrostHeader,
  useWorkflowMutation,
  useWorkflowQuery,
  type BifrostNavLinkProps,
} from "bifrost";

import {
  connectionState,
  migrationProgress,
  type MigrationItemStatus,
} from "./lib/migrationPresentation";

const REF = {
  overview: "functions/migration.py::migration_get_overview",
  create: "functions/migration.py::migration_create",
  validate: "functions/migration.py::migration_validate_connection",
  export: "functions/migration.py::migration_export_environment",
  context: "functions/migration.py::migration_get_context",
  savePlan: "functions/migration.py::migration_save_plan",
  updateItem: "functions/migration.py::migration_update_item",
  artifact: "functions/migration.py::migration_read_artifact",
} as const;

type Connection = {
  api_url: string | null;
  root_org_id: string | null;
  cookie_configured: boolean;
};
type MigrationSummary = {
  id: string;
  name: string;
  mode: "sample" | "live";
  status: string;
  updated_at: string | null;
  item_count: number;
  coverage_gaps: string[];
};
type Overview = {
  solution_id?: string | null;
  connection: Connection;
  migrations: MigrationSummary[];
  capabilities: { live_verified: boolean };
};
type MigrationItem = {
  id: string;
  label?: string;
  name?: string;
  kind?: string;
  status: MigrationItemStatus;
  evidence?: string | null;
  revision?: number;
};
type CoverageItem = {
  resource: string;
  scope: string;
  status: string;
  count: number;
  detail: string;
};
type MigrationContext = {
  solution_id?: string | null;
  migration: MigrationSummary;
  plan: { markdown: string; revision: number };
  items: MigrationItem[];
  coverage: CoverageItem[];
  artifacts: Array<{ id: string; label: string; kind: string }>;
  handoff: { prompt: string; commands: string[] };
};

const NAV = [
  { to: "/", label: "Setup", end: true },
  { to: "/inventory", label: "Inventory" },
  { to: "/plan", label: "Plan" },
  { to: "/agent-handoff", label: "Agent Handoff" },
];

function HeaderNavLink(props: BifrostNavLinkProps) {
  return <NavLink {...props} />;
}

function Status({
  children,
  tone = "neutral",
}: {
  children: string;
  tone?: "neutral" | "warning" | "success";
}) {
  const palette = {
    neutral: "border-border bg-muted text-muted-foreground",
    warning:
      "border-amber-500/50 bg-amber-500/10 text-amber-800 dark:text-amber-200",
    success:
      "border-emerald-600/45 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200",
  };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${palette[tone]}`}
    >
      {children}
    </span>
  );
}

function Button({
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
function SecondaryButton({
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

type PlanDraft = { markdown: string; revision: number };
type AppContext = {
  overview: Overview;
  selectedId: string | null;
  startSample: () => Promise<void>;
  createLoading: boolean;
  startLive: (name: string, sourceOrgIds: string[]) => Promise<void>;
  validatedOrganizations: Array<{ id: string; name: string }>;
  validateConnection: () => Promise<void>;
  validateLoading: boolean;
  actionMessage: string | null;
  actionError: Error | null;
  refreshOverview: () => Promise<Overview>;
  drafts: Record<string, PlanDraft>;
  rememberDraft: (migrationId: string, draft: PlanDraft | null) => void;
};
function useAppContext() {
  return useOutletContext<AppContext>();
}

function Shell() {
  return (
    <div className="flex h-full min-h-0 flex-col bg-background text-foreground">
      <BifrostHeader
        title="Rewst Migration"
        nav={{ items: NAV, link: HeaderNavLink }}
      />
      <main className="min-h-0 flex-1 overflow-auto">
        <MigrationController />
      </main>
    </div>
  );
}

function MigrationController() {
  const navigate = useNavigate();
  const overview = useWorkflowQuery<Overview>(REF.overview);
  const create = useWorkflowMutation<{ migration: MigrationSummary }>(
    REF.create,
  );
  const validate = useWorkflowMutation<{
    ok: boolean;
    message: string;
    organizations: Array<{ id: string; name: string }>;
  }>(REF.validate);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, PlanDraft>>({});
  const rememberDraft = (migrationId: string, draft: PlanDraft | null) => {
    setDrafts((current) => {
      const next = { ...current };
      if (draft) next[migrationId] = draft;
      else delete next[migrationId];
      return next;
    });
  };
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [validatedOrganizations, setValidatedOrganizations] = useState<
    Array<{ id: string; name: string }>
  >([]);
  useEffect(() => {
    if (!selectedId && overview.data?.migrations[0])
      setSelectedId(overview.data.migrations[0].id);
  }, [overview.data, selectedId]);
  const startSample = async () => {
    setActionMessage(null);
    try {
      const result = await create.mutate({
        name: "Sample Rewst migration",
        mode: "sample",
      });
      setSelectedId(result.migration.id);
      await overview.refresh();
      setActionMessage(
        "Sample migration created. Review its exported inventory next.",
      );
      navigate("/inventory");
    } catch {
      /* rendered next to action */
    }
  };
  const startLive = async (name: string, sourceOrgIds: string[]) => {
    setActionMessage(null);
    try {
      const result = await create.mutate({
        name,
        mode: "live",
        source_org_ids: sourceOrgIds,
      });
      setSelectedId(result.migration.id);
      await overview.refresh();
      setActionMessage(
        "Live migration created. Review the inventory before planning any implementation work.",
      );
      navigate("/inventory");
    } catch {
      /* rendered next to action */
    }
  };
  const validateConnection = async () => {
    setActionMessage(null);
    try {
      const result = await validate.mutate({});
      setValidatedOrganizations(result.organizations ?? []);
      await overview.refresh();
      setActionMessage(
        result.message ||
          (result.ok
            ? "Connection check completed."
            : "Connection could not be verified."),
      );
    } catch {
      /* rendered next to action */
    }
  };
  if (overview.loading && !overview.data)
    return <LoadingPage label="Loading migration setup…" />;
  if (overview.error && !overview.data)
    return <ErrorPage onRetry={() => void overview.refresh()} />;
  if (!overview.data)
    return <ErrorPage onRetry={() => void overview.refresh()} />;
  const selected =
    overview.data.migrations.find((migration) => migration.id === selectedId) ??
    overview.data.migrations[0];
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 md:px-6 md:py-8">
      {overview.data.migrations.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3 border-b border-border pb-4">
          <label className="text-sm font-medium" htmlFor="migration-picker">
            Migration
          </label>
          <select
            id="migration-picker"
            value={selected?.id ?? ""}
            onChange={(event) => setSelectedId(event.target.value)}
            className="min-h-10 max-w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            {overview.data.migrations.map((migration) => (
              <option key={migration.id} value={migration.id}>
                {migration.name}
              </option>
            ))}
          </select>
          {selected && (
            <Status tone={selected.mode === "sample" ? "neutral" : "warning"}>
              {selected.mode === "sample"
                ? "Sample mode"
                : "Live mode — unverified"}
            </Status>
          )}
        </div>
      )}
      <Outlet
        context={
          {
            overview: overview.data,
            selectedId,
            startSample,
            startLive,
            validatedOrganizations,
            createLoading: create.loading,
            validateConnection,
            validateLoading: validate.loading,
            actionMessage,
            actionError: create.error ?? validate.error,
            refreshOverview: overview.refresh,
            drafts,
            rememberDraft,
          } satisfies AppContext
        }
      />
    </div>
  );
}

function SetupPage() {
  const context = useAppContext();
  const status = connectionState(context.overview.connection);
  const configHref = context.overview.solution_id
    ? `/solutions/${context.overview.solution_id}?tab=configuration`
    : "/solutions";
  const [liveName, setLiveName] = useState("Rewst migration");
  const [sourceOrgIds, setSourceOrgIds] = useState<string[]>([]);
  const toggleSourceOrg = (id: string) =>
    setSourceOrgIds((current) =>
      current.includes(id)
        ? current.filter((currentId) => currentId !== id)
        : [...current, id],
    );
  return (
    <div className="space-y-8">
      <header className="max-w-3xl space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Status tone={status.tone}>{status.label}</Status>
          <Status
            tone={
              context.overview.capabilities.live_verified
                ? "success"
                : "warning"
            }
          >
            {context.overview.capabilities.live_verified
              ? "Live compatibility verified"
              : "Live compatibility unverified"}
          </Status>
        </div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] md:text-3xl">
          Prepare a Rewst migration
        </h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground md:text-base">
          Start safely with a representative sample. Live-mode compatibility is
          not yet verified, and this Solution does not perform a production
          cutover.
        </p>
      </header>
      <section
        aria-labelledby="sample-heading"
        className="border-y border-border py-6"
      >
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-2xl space-y-2">
            <h2 id="sample-heading" className="text-lg font-semibold">
              Try a Sample Migration
            </h2>
            <p className="text-sm leading-6 text-muted-foreground">
              Create a local planning record, export a sample inventory, and
              hand its context to an implementation agent.
            </p>
          </div>
          <Button
            onClick={() => void context.startSample()}
            disabled={context.createLoading}
            aria-busy={context.createLoading}
          >
            {context.createLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Sparkles className="h-4 w-4" aria-hidden="true" />
            )}
            {context.createLoading
              ? "Creating sample…"
              : "Try a Sample Migration"}
          </Button>
        </div>
        {context.actionError && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {context.actionError.message ||
              "Could not create or verify the migration. Retry, or check the Solution configuration."}
          </p>
        )}
        {context.actionMessage && (
          <p
            role="status"
            className="mt-3 text-sm text-emerald-700 dark:text-emerald-300"
          >
            {context.actionMessage}
          </p>
        )}
      </section>
      <section
        aria-labelledby="connection-heading"
        className="grid gap-6 border-b border-border pb-8 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,0.7fr)]"
      >
        <div className="space-y-3">
          <h2 id="connection-heading" className="text-lg font-semibold">
            Connect your Rewst environment
          </h2>
          <p className="text-sm leading-6 text-muted-foreground">
            Enter connection values in this Solution’s native Configuration tab.
            The app only checks their server-side result; it never displays or
            accepts a session token in the browser.
          </p>
          <a
            href={configHref}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-10 items-center gap-2 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            Open Solution Configuration{" "}
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
          </a>
          {!context.overview.solution_id && (
            <p className="text-xs text-muted-foreground">
              Open Rewst Migration in Solutions, then choose Configuration.
            </p>
          )}
        </div>
        <dl className="space-y-3 rounded-lg bg-muted/45 p-4 text-sm">
          <div>
            <dt className="font-medium">rewst_migration_api_url</dt>
            <dd className="mt-1 text-muted-foreground">
              Rewst GraphQL URL. Historical default:
              https://api.rewst.io/graphql
            </dd>
          </div>
          <div>
            <dt className="font-medium">rewst_migration_org_id</dt>
            <dd className="mt-1 text-muted-foreground">
              The root organization ID to inspect.
            </dd>
          </div>
          <div>
            <dt className="font-medium">rewst_migration_app_session</dt>
            <dd className="mt-1 text-muted-foreground">
              Secret session value. Fetch it manually from browser DevTools; do
              not paste it into this app.
            </dd>
          </div>
        </dl>
      </section>
      <section
        aria-labelledby="verify-heading"
        className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
      >
        <div>
          <h2 id="verify-heading" className="font-semibold">
            Verify the configured connection
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            A configured secret is not proof of working access. Verification
            checks the configured environment.
          </p>
        </div>
        <SecondaryButton
          onClick={() => void context.validateConnection()}
          disabled={context.validateLoading}
          aria-busy={context.validateLoading}
        >
          {context.validateLoading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          )}
          {context.validateLoading ? "Checking…" : "Verify connection"}
        </SecondaryButton>
      </section>
      {context.validatedOrganizations.length > 0 && (
        <section
          aria-labelledby="live-heading"
          className="space-y-4 border-t border-border pt-6"
        >
          <div>
            <h2 id="live-heading" className="text-lg font-semibold">
              Create a live migration
            </h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Select the verified source organizations to inspect. Live
              compatibility remains unverified, and no production cutover is
              included.
            </p>
          </div>
          <label
            className="block text-sm font-medium"
            htmlFor="live-migration-name"
          >
            Migration name
            <input
              id="live-migration-name"
              value={liveName}
              onChange={(event) => setLiveName(event.target.value)}
              className="mt-1.5 min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-normal outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
          <fieldset>
            <legend className="text-sm font-medium">
              Source organizations
            </legend>
            <div className="mt-2 space-y-2">
              {context.validatedOrganizations.map((organization) => (
                <label
                  key={organization.id}
                  className="flex min-h-10 items-center gap-3 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={sourceOrgIds.includes(organization.id)}
                    onChange={() => toggleSourceOrg(organization.id)}
                    className="h-4 w-4 accent-primary"
                  />
                  {organization.name}
                </label>
              ))}
            </div>
          </fieldset>
          <Button
            onClick={() =>
              void context.startLive(
                liveName.trim() || "Rewst migration",
                sourceOrgIds,
              )
            }
            disabled={context.createLoading || sourceOrgIds.length === 0}
            aria-busy={context.createLoading}
          >
            {context.createLoading
              ? "Creating live migration…"
              : "Create live migration"}
          </Button>
          {sourceOrgIds.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Select at least one source organization to create a live
              migration.
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function MigrationRequired({
  children,
}: {
  children: (value: {
    migrationId: string;
    context: MigrationContext;
    refresh: () => Promise<MigrationContext>;
  }) => ReactNode;
}) {
  const app = useAppContext();
  const selected =
    app.overview.migrations.find(
      (migration) => migration.id === app.selectedId,
    ) ?? app.overview.migrations[0];
  if (!selected)
    return (
      <EmptyMigrations
        onStart={() => void app.startSample()}
        loading={app.createLoading}
      />
    );
  return (
    <MigrationData key={selected.id} migrationId={selected.id}>
      {children}
    </MigrationData>
  );
}
function MigrationData({
  migrationId,
  children,
}: {
  migrationId: string;
  children: (value: {
    migrationId: string;
    context: MigrationContext;
    refresh: () => Promise<MigrationContext>;
  }) => ReactNode;
}) {
  const query = useWorkflowQuery<MigrationContext>(REF.context, {
    migration_id: migrationId,
  });
  if (query.loading && !query.data)
    return <LoadingPage label="Loading migration context…" />;
  if (query.error && !query.data)
    return <ErrorPage onRetry={() => void query.refresh()} />;
  if (!query.data) return <ErrorPage onRetry={() => void query.refresh()} />;
  return (
    <>
      {children({ migrationId, context: query.data, refresh: query.refresh })}
    </>
  );
}

function InventoryPage() {
  const app = useAppContext();
  const navigate = useNavigate();
  const exportEnvironment = useWorkflowMutation(REF.export);
  return (
    <MigrationRequired>
      {({ migrationId, context, refresh }) => {
        const exportInventory = async () => {
          try {
            await exportEnvironment.mutate({ migration_id: migrationId });
            await Promise.all([refresh(), app.refreshOverview()]);
          } catch {
            /* rendered below */
          }
        };
        const gaps = context.migration.coverage_gaps ?? [];
        return (
          <div className="space-y-7">
            <header className="flex flex-col gap-4 border-b border-border pb-6 md:flex-row md:items-end md:justify-between">
              <div className="min-w-0 space-y-2">
                <Status
                  tone={
                    context.migration.mode === "sample" ? "neutral" : "warning"
                  }
                >
                  {context.migration.mode === "sample"
                    ? "Sample mode"
                    : "Live mode — unverified"}
                </Status>
                <h1 className="truncate text-2xl font-semibold tracking-[-0.02em]">
                  {context.migration.name}
                </h1>
                <p className="text-sm text-muted-foreground">
                  Inspect discovered objects before defining the implementation
                  plan.
                </p>
              </div>
              {context.migration.status === "exported" ? (
                <SecondaryButton onClick={() => navigate("/")}>
                  Start a new migration
                </SecondaryButton>
              ) : (
                <Button
                  onClick={() => void exportInventory()}
                  disabled={exportEnvironment.loading}
                  aria-busy={exportEnvironment.loading}
                >
                  {exportEnvironment.loading ? (
                    <Loader2
                      className="h-4 w-4 animate-spin"
                      aria-hidden="true"
                    />
                  ) : (
                    <Download className="h-4 w-4" aria-hidden="true" />
                  )}
                  {exportEnvironment.loading
                    ? "Exporting…"
                    : "Export environment"}
                </Button>
              )}
            </header>
            {exportEnvironment.error && (
              <p role="alert" className="text-sm text-destructive">
                The inventory export failed. Retry after checking the connection
                configuration.
              </p>
            )}
            <section aria-labelledby="inventory-heading" className="space-y-3">
              <div className="flex items-baseline justify-between gap-4">
                <h2 id="inventory-heading" className="text-lg font-semibold">
                  Inventory
                </h2>
                <span className="text-sm text-muted-foreground">
                  {context.items.length} items
                </span>
              </div>
              {context.items.length === 0 ? (
                <p className="rounded-lg bg-muted/50 p-5 text-sm text-muted-foreground">
                  No items have been exported yet. Export the environment to
                  create an inventory.
                </p>
              ) : (
                <ul className="divide-y divide-border border-y border-border">
                  {context.items.map((item) => (
                    <li key={item.id} className="py-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="font-medium">
                            {item.label ?? item.name ?? item.id}
                          </p>
                          <p className="mt-1 text-sm text-muted-foreground">
                            {item.kind ?? "Migration item"}
                          </p>
                        </div>
                        <Status
                          tone={
                            item.status === "tested" ? "success" : "neutral"
                          }
                        >
                          {item.status}
                        </Status>
                      </div>
                      {item.evidence && (
                        <p className="mt-3 text-sm text-muted-foreground">
                          Evidence: {item.evidence}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section
              aria-labelledby="coverage-heading"
              className="border-t border-border pt-6"
            >
              <h2 id="coverage-heading" className="text-lg font-semibold">
                Coverage gaps
              </h2>
              {gaps.length ? (
                <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                  {gaps.map((gap) => (
                    <li key={gap} className="flex gap-2">
                      <CircleAlert
                        className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-300"
                        aria-hidden="true"
                      />
                      {gap}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">
                  No coverage gaps reported by the latest export.
                </p>
              )}
              {context.coverage.length > 0 && (
                <div className="mt-4 overflow-x-auto rounded-lg border border-border">
                  <table className="w-full min-w-[34rem] text-left text-sm">
                    <thead className="border-b border-border bg-muted/45 text-xs uppercase tracking-wide text-muted-foreground">
                      <tr>
                        <th className="px-4 py-3 font-medium">Resource</th>
                        <th className="px-4 py-3 font-medium">Scope</th>
                        <th className="px-4 py-3 font-medium">Status</th>
                        <th className="px-4 py-3 font-medium">Count</th>
                        <th className="px-4 py-3 font-medium">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {context.coverage.map((entry) => (
                        <tr
                          key={`${entry.resource}:${entry.scope}`}
                          className="border-b border-border last:border-0"
                        >
                          <td className="px-4 py-3 font-medium">
                            {entry.resource}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {entry.scope}
                          </td>
                          <td className="px-4 py-3">
                            <Status
                              tone={
                                entry.status === "complete"
                                  ? "success"
                                  : "warning"
                              }
                            >
                              {entry.status}
                            </Status>
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {entry.count}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {entry.detail}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <div className="flex justify-end">
              <Button onClick={() => navigate("/plan")}>
                Build implementation plan{" "}
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </div>
        );
      }}
    </MigrationRequired>
  );
}

function PlanPage() {
  return (
    <MigrationRequired>
      {({ migrationId, context, refresh }) => (
        <PlanEditor
          key={migrationId}
          migrationId={migrationId}
          context={context}
          refresh={refresh}
        />
      )}
    </MigrationRequired>
  );
}
function PlanEditor({
  migrationId,
  context,
  refresh,
}: {
  migrationId: string;
  context: MigrationContext;
  refresh: () => Promise<MigrationContext>;
}) {
  const app = useAppContext();
  const savePlan = useWorkflowMutation(REF.savePlan);
  const updateItem = useWorkflowMutation(REF.updateItem);
  const remembered = app.drafts[migrationId];
  const [draft, setDraft] = useState(
    remembered?.markdown ?? context.plan.markdown,
  );
  const [dirty, setDirty] = useState(Boolean(remembered));
  const [draftRevision, setDraftRevision] = useState(
    remembered?.revision ?? context.plan.revision,
  );
  const [mergeConfirmed, setMergeConfirmed] = useState(false);
  const [evidence, setEvidence] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const progress = useMemo(
    () => migrationProgress(context.items),
    [context.items],
  );
  useEffect(() => {
    if (!dirty) {
      setDraft(context.plan.markdown);
      setDraftRevision(context.plan.revision);
      setMergeConfirmed(false);
    }
  }, [context.plan.markdown, context.plan.revision, dirty]);
  const save = async () => {
    setNotice(null);
    if (context.plan.revision !== draftRevision && !mergeConfirmed) {
      setNotice(
        "Compare the latest saved plan below and confirm your merge before saving.",
      );
      return;
    }
    try {
      await savePlan.mutate({
        migration_id: migrationId,
        markdown: draft,
        expected_revision: context.plan.revision,
      });
      setNotice("Plan saved.");
      await refresh();
      setDirty(false);
      app.rememberDraft(migrationId, null);
    } catch {
      setNotice(
        "The plan may have changed elsewhere. Your unsaved draft is still here; reload the context before deciding how to merge it.",
      );
    }
  };
  const changeItem = async (
    item: MigrationItem,
    status: MigrationItemStatus,
  ) => {
    const itemEvidence = evidence[item.id] ?? item.evidence ?? "";
    if (status === "tested" && !itemEvidence.trim()) {
      setNotice("Add verification evidence before marking an item tested.");
      return;
    }
    setNotice(null);
    try {
      await updateItem.mutate({
        migration_id: migrationId,
        item_id: item.id,
        status,
        evidence: itemEvidence,
        expected_revision: item.revision ?? context.plan.revision,
      });
      await refresh();
    } catch {
      setNotice("Item status was not saved. Refresh the context and retry.");
    }
  };
  return (
    <div className="space-y-7">
      <header className="border-b border-border pb-6">
        <h1 className="text-2xl font-semibold tracking-[-0.02em]">
          Implementation plan
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Define reviewable migration work. Tested items require evidence;
          production cutover is outside this Solution.
        </p>
      </header>
      <section aria-labelledby="plan-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="plan-heading" className="text-lg font-semibold">
              Plan notes
            </h2>
            <p className="text-sm text-muted-foreground">
              Latest saved revision {context.plan.revision}
              {dirty ? " · unsaved draft" : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <SecondaryButton
              onClick={() => void refresh()}
              disabled={savePlan.loading}
            >
              Refresh latest plan
            </SecondaryButton>
            <Button
              onClick={() => void save()}
              disabled={savePlan.loading}
              aria-busy={savePlan.loading}
            >
              {savePlan.loading ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileText className="h-4 w-4" aria-hidden="true" />
              )}
              {savePlan.loading ? "Saving…" : "Save plan"}
            </Button>
          </div>
        </div>
        <label className="sr-only" htmlFor="migration-plan">
          Migration plan
        </label>
        <textarea
          id="migration-plan"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setDirty(true);
            app.rememberDraft(migrationId, {
              markdown: event.target.value,
              revision: draftRevision,
            });
          }}
          className="min-h-64 w-full rounded-md border border-input bg-background p-3 font-mono text-sm leading-6 outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        {dirty && context.plan.revision !== draftRevision && (
          <div className="space-y-3 rounded-md border border-border p-4">
            <p className="text-sm font-medium">
              A newer plan was saved. Your draft is preserved above.
            </p>
            <pre
              aria-label="Latest saved plan"
              className="max-h-64 overflow-auto whitespace-pre-wrap text-sm"
            >
              {context.plan.markdown}
            </pre>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={mergeConfirmed}
                onChange={(event) => setMergeConfirmed(event.target.checked)}
              />
              I compared the latest plan and merged the changes into my draft.
            </label>
          </div>
        )}
      </section>
      {(savePlan.error || updateItem.error || notice) && (
        <p
          role={savePlan.error || updateItem.error ? "alert" : "status"}
          className={`text-sm ${savePlan.error || updateItem.error ? "text-destructive" : "text-muted-foreground"}`}
        >
          {notice ??
            "The requested change could not be saved. Retry after refreshing."}
        </p>
      )}
      <section aria-labelledby="work-heading" className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 id="work-heading" className="text-lg font-semibold">
            Tracked work
          </h2>
          <p className="text-sm text-muted-foreground">
            {progress.tested} tested · {progress.remaining} remaining
          </p>
        </div>
        {context.items.length === 0 ? (
          <p className="rounded-lg bg-muted/50 p-5 text-sm text-muted-foreground">
            Export an environment before tracking implementation work.
          </p>
        ) : (
          <div className="space-y-3">
            {context.items.map((item) => (
              <article
                key={item.id}
                className="border-b border-border pb-4 last:border-0"
              >
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="min-w-0">
                    <h3 className="font-medium">
                      {item.label ?? item.name ?? item.id}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Current state: {item.status}
                    </p>
                  </div>
                  <label className="text-sm">
                    <span className="sr-only">
                      State for {item.label ?? item.name ?? item.id}
                    </span>
                    <select
                      value={item.status}
                      onChange={(event) =>
                        void changeItem(
                          item,
                          event.target.value as MigrationItemStatus,
                        )
                      }
                      disabled={updateItem.loading}
                      className="min-h-10 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    >
                      <option value="discovered">Discovered</option>
                      <option value="planned">Planned</option>
                      <option value="implementing">Implementing</option>
                      <option value="tested">Tested</option>
                    </select>
                  </label>
                </div>
                <label className="mt-3 block text-sm font-medium">
                  Verification evidence{" "}
                  <span className="font-normal text-muted-foreground">
                    (required for tested)
                  </span>
                  <input
                    value={evidence[item.id] ?? item.evidence ?? ""}
                    onChange={(event) =>
                      setEvidence((current) => ({
                        ...current,
                        [item.id]: event.target.value,
                      }))
                    }
                    className="mt-1.5 min-h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-normal outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    placeholder="Link, test run, or review note"
                  />
                </label>
                <SecondaryButton
                  className="mt-3"
                  disabled={updateItem.loading}
                  onClick={() => void changeItem(item, item.status)}
                >
                  Save evidence for {item.label ?? item.name ?? item.id}
                </SecondaryButton>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ArtifactContent({ content }: { content: unknown }) {
  return (
    <pre
      aria-label="Artifact content"
      className="max-h-96 overflow-auto rounded-lg bg-muted/55 p-4 text-sm leading-6"
    >
      {typeof content === "string" ? content : JSON.stringify(content, null, 2)}
    </pre>
  );
}
function AgentHandoffPage() {
  const readArtifact = useWorkflowMutation<{
    id: string;
    content: unknown;
    format: string;
  }>(REF.artifact);
  const [copied, setCopied] = useState<string | null>(null);
  return (
    <MigrationRequired>
      {({ migrationId, context }) => {
        const copy = async (label: string, content: string) => {
          try {
            await navigator.clipboard.writeText(content);
            setCopied(label);
          } catch {
            setCopied("Copy failed — select the text and copy it manually.");
          }
        };
        return (
          <div className="space-y-7">
            <header className="border-b border-border pb-6">
              <h1 className="text-2xl font-semibold tracking-[-0.02em]">
                Agent handoff
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                Use this context with an external Codex or Claude session.
                Bifrost keeps the migration record and artifacts.
              </p>
            </header>
            <section aria-labelledby="prompt-heading" className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="prompt-heading" className="text-lg font-semibold">
                  Handoff prompt
                </h2>
                <SecondaryButton
                  onClick={() =>
                    void copy("Handoff prompt copied.", context.handoff.prompt)
                  }
                >
                  <Clipboard className="h-4 w-4" aria-hidden="true" />
                  Copy prompt
                </SecondaryButton>
              </div>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-muted/55 p-4 text-sm leading-6 text-foreground">
                {context.handoff.prompt ||
                  "No handoff prompt is available yet. Export the environment and save a plan first."}
              </pre>
            </section>
            <section
              aria-labelledby="commands-heading"
              className="space-y-3 border-t border-border pt-6"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="commands-heading" className="text-lg font-semibold">
                  Suggested commands
                </h2>
                <SecondaryButton
                  onClick={() =>
                    void copy(
                      "Commands copied.",
                      context.handoff.commands.join("\n"),
                    )
                  }
                  disabled={!context.handoff.commands.length}
                >
                  <Clipboard className="h-4 w-4" aria-hidden="true" />
                  Copy commands
                </SecondaryButton>
              </div>
              {context.handoff.commands.length ? (
                <pre
                  aria-label="Suggested CLI commands"
                  className="overflow-auto whitespace-pre-wrap rounded-lg bg-muted/55 p-4 text-sm leading-6 [overflow-wrap:anywhere]"
                >
                  {context.handoff.commands.join("\n")}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Commands appear after an environment export.
                </p>
              )}
            </section>
            <section
              aria-labelledby="artifacts-heading"
              className="space-y-3 border-t border-border pt-6"
            >
              <h2 id="artifacts-heading" className="text-lg font-semibold">
                Migration artifacts
              </h2>
              {context.artifacts.length ? (
                <ul className="divide-y divide-border border-y border-border">
                  {context.artifacts.map((artifact) => (
                    <li
                      key={artifact.id}
                      className="flex flex-wrap items-center justify-between gap-3 py-3"
                    >
                      <div>
                        <p className="font-medium">{artifact.label}</p>
                        <p className="text-sm text-muted-foreground">
                          {artifact.kind}
                        </p>
                      </div>
                      <SecondaryButton
                        onClick={() =>
                          void readArtifact.mutate({
                            migration_id: migrationId,
                            artifact_id: artifact.id,
                          })
                        }
                        disabled={readArtifact.loading}
                      >
                        {readArtifact.loading ? "Loading…" : "Read artifact"}
                      </SecondaryButton>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No artifacts yet. Export the environment to create them.
                </p>
              )}
              {readArtifact.error && (
                <p role="alert" className="text-sm text-destructive">
                  Could not load that artifact. Try again.
                </p>
              )}
              {readArtifact.data &&
                context.artifacts.some(
                  (artifact) => artifact.id === readArtifact.data?.id,
                ) && <ArtifactContent content={readArtifact.data.content} />}
            </section>
            {copied && (
              <p
                role="status"
                className="text-sm text-emerald-700 dark:text-emerald-300"
              >
                {copied}
              </p>
            )}
          </div>
        );
      }}
    </MigrationRequired>
  );
}

function EmptyMigrations({
  onStart,
  loading,
}: {
  onStart: () => void;
  loading: boolean;
}) {
  return (
    <section className="max-w-2xl space-y-4 py-10">
      <h1 className="text-2xl font-semibold tracking-[-0.02em]">
        No migration selected
      </h1>
      <p className="text-sm leading-6 text-muted-foreground">
        Start with a sample migration to generate an inventory and
        implementation plan.
      </p>
      <Button onClick={onStart} disabled={loading}>
        {loading ? "Creating sample…" : "Try a Sample Migration"}
      </Button>
    </section>
  );
}
function LoadingPage({ label }: { label: string }) {
  return (
    <div
      role="status"
      className="flex items-center gap-3 py-12 text-sm text-muted-foreground"
    >
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      {label}
    </div>
  );
}
function ErrorPage({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="max-w-xl space-y-4 py-10">
      <h1 className="text-xl font-semibold">Migration data is unavailable</h1>
      <p className="text-sm leading-6 text-muted-foreground">
        Refresh the data or check the Solution configuration and workflow
        access.
      </p>
      <SecondaryButton onClick={onRetry}>
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Retry
      </SecondaryButton>
    </section>
  );
}
function NotFound() {
  return (
    <section className="space-y-3 py-10">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Link to="/" className="text-sm font-medium text-primary hover:underline">
        Return to setup
      </Link>
    </section>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<SetupPage />} />
        <Route path="inventory" element={<InventoryPage />} />
        <Route path="plan" element={<PlanPage />} />
        <Route path="agent-handoff" element={<AgentHandoffPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
