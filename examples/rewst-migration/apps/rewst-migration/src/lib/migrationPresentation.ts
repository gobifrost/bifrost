export type Connection = {
  api_url: string | null;
  root_org_id: string | null;
  cookie_configured: boolean;
};

export type MigrationItemStatus =
  | "discovered"
  | "planned"
  | "implementing"
  | "tested";

export function connectionState(connection: Connection) {
  if (
    !connection.api_url ||
    !connection.root_org_id ||
    !connection.cookie_configured
  ) {
    return {
      label: "Setup required",
      tone: "warning" as const,
      action: "Configure connection",
    };
  }
  return {
    label: "Connection not verified",
    tone: "warning" as const,
    action: "Verify connection",
  };
}

export function migrationProgress(items: Array<{ status: string }>) {
  const tested = items.filter((item) => item.status === "tested").length;
  return { total: items.length, tested, remaining: items.length - tested };
}
