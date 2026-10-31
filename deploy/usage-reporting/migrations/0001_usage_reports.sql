CREATE TABLE usage_reports (
  installation_id TEXT NOT NULL,
  period TEXT NOT NULL,
  received_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  version TEXT NOT NULL,
  workflow_runs INTEGER NOT NULL,
  agent_runs INTEGER NOT NULL,
  PRIMARY KEY (installation_id, period)
);

CREATE INDEX usage_reports_period_idx ON usage_reports (period);
