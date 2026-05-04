import { useTable, useWorkflowMutation, useUser } from "bifrost";

const TABLE_NAME = "progress_demo";
const WORKFLOW_ID = "8aca1e6c-bf33-4b7b-b385-cb6274c4daa9";

// Provider/Beta org IDs are the well-known UUIDs the spot-check seed script
// installs. They're used purely for displaying a friendly org name in the
// identity panel — the actual access decision happens server-side based on
// the user's session.
const PROVIDER_ORG_ID = "00000000-0000-0000-0000-000000000002";
const BETA_ORG_ID = "00000000-0000-0000-0000-000000000003";
const ORG_LABELS: Record<string, string> = {
  [PROVIDER_ORG_ID]: "Provider",
  [BETA_ORG_ID]: "Beta",
};

interface ProgressRow {
  id: string;
  table_id?: string;
  step: number;
  total_steps: number;
  message: string;
  status: string;
  progress_pct: number;
  created_at: string;
}

export default function Home() {
  const user = useUser();
  const { rows, loading, error } = useTable(TABLE_NAME);
  const {
    execute,
    isLoading,
    error: runError,
  } = useWorkflowMutation<{ inserted: number; ids: string[]; message: string }>(
    WORKFLOW_ID,
  );

  // Swallow the rejected promise so React doesn't log it as unhandled — the
  // identity panel already surfaces `runError` to the user.
  const onRun = () => {
    void execute({}).catch(() => {});
  };

  // Sort by step ascending so the visual progress reads top-to-bottom.
  const ordered = [...rows].sort((a: any, b: any) => (a.step ?? 0) - (b.step ?? 0));

  const orgLabel = ORG_LABELS[user.organizationId] ?? user.organizationId ?? "(no org)";
  const resolvedTableId = (rows[0] as ProgressRow | undefined)?.table_id ?? null;
  // useWorkflowMutation surfaces errors as a plain string, not an Error.
  const runErrMsg = runError ?? null;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 20px" }}>
      <header style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 28, margin: 0, color: "#f1f5f9" }}>Progress Demo</h1>
        <p style={{ color: "#94a3b8", marginTop: 4 }}>
          A workflow inserts 5 rows into the <code>progress_demo</code> table over ~5 seconds.
          Each row arrives via WebSocket — no refetching, no polling.
        </p>
      </header>

      {/* Identity / access panel — makes the org gate visible to a manual reviewer. */}
      <div
        data-testid="identity-panel"
        style={{
          padding: "12px 16px",
          marginBottom: 20,
          background: "#0b1220",
          border: "1px solid #1e293b",
          borderRadius: 8,
          fontSize: 13,
          lineHeight: 1.6,
        }}
      >
        <div>
          <span style={{ color: "#64748b" }}>Signed in as: </span>
          <span style={{ color: "#f1f5f9", fontWeight: 600 }}>
            {user.email || "(unauthenticated)"}
          </span>
        </div>
        <div>
          <span style={{ color: "#64748b" }}>Org: </span>
          <span style={{ color: "#f1f5f9" }}>{orgLabel}</span>
        </div>
        <div>
          <span style={{ color: "#64748b" }}>Rows visible: </span>
          <span style={{ color: "#f1f5f9", fontWeight: 600 }}>{rows.length}</span>
        </div>
        {resolvedTableId && (
          <div>
            <span style={{ color: "#64748b" }}>Resolved table_id: </span>
            <code style={{ color: "#94a3b8", fontSize: 12 }}>{resolvedTableId}</code>
          </div>
        )}
      </div>

      <button
        onClick={onRun}
        disabled={isLoading}
        style={{
          padding: "10px 18px",
          fontSize: 14,
          fontWeight: 600,
          color: "#0f172a",
          background: isLoading ? "#94a3b8" : "#38bdf8",
          border: "none",
          borderRadius: 6,
          cursor: isLoading ? "wait" : "pointer",
          marginBottom: 16,
        }}
      >
        {isLoading ? "Running…" : "Run workflow"}
      </button>

      {runErrMsg && (
        <div
          data-testid="run-error-banner"
          style={{
            padding: "10px 12px",
            marginBottom: 20,
            background: "#450a0a",
            border: "1px solid #7f1d1d",
            borderRadius: 6,
            color: "#fecaca",
            fontSize: 13,
          }}
        >
          Workflow refused: {runErrMsg}
        </div>
      )}

      {loading && <p style={{ color: "#94a3b8" }}>Loading initial snapshot…</p>}
      {error && (
        <p style={{ color: "#f87171" }}>Error: {String(error.message ?? error)}</p>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {ordered.length === 0 && !loading && (
          <p style={{ color: "#64748b", fontStyle: "italic" }}>
            No rows yet. Click "Run workflow" to populate.
          </p>
        )}
        {ordered.map((r: any) => {
          const row = r as ProgressRow;
          const isDone = row.status === "done";
          const barColor = isDone ? "#22c55e" : "#38bdf8";
          return (
            <div
              key={row.id}
              style={{
                padding: "12px 16px",
                background: "#1e293b",
                borderRadius: 8,
                borderLeft: `4px solid ${barColor}`,
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  marginBottom: 6,
                }}
              >
                <span style={{ fontWeight: 600, color: "#f1f5f9" }}>
                  Step {row.step}/{row.total_steps}: {row.message}
                </span>
                <span style={{ color: barColor, fontSize: 13 }}>
                  {row.progress_pct}% • {row.status}
                </span>
              </div>
              <div
                style={{
                  height: 6,
                  background: "#0f172a",
                  borderRadius: 3,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${row.progress_pct}%`,
                    height: "100%",
                    background: barColor,
                    transition: "width 200ms ease",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
