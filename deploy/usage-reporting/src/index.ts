interface Env {
  DB: D1Database;
}

interface UsageReport {
  schema_version: 1;
  installation_id: string;
  version: string;
  period: string;
  workflow_runs: number;
  agent_runs: number;
  environment: "production";
}

const MAX_BODY_BYTES = 4096;
const MAX_RUN_COUNT = 1_000_000_000;
const MAX_VERSION_LENGTH = 128;
const DAY_MS = 86_400_000;
const ALLOWED_FIELDS = new Set([
  "schema_version",
  "installation_id",
  "version",
  "period",
  "workflow_runs",
  "agent_runs",
  "environment",
]);
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:(?:0|[1-9]\d*)|(?:[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))(?:\.(?:(?:0|[1-9]\d*)|(?:[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;

function status(code: 400 | 413 | 415 | 503): Response {
  const messages: Record<typeof code, string> = {
    400: "Bad request",
    413: "Request body too large",
    415: "Unsupported media type",
    503: "Service unavailable",
  };
  return new Response(messages[code], { status: code });
}

function acceptsJson(contentType: string | null): boolean {
  return contentType !== null && /^application\/json(?:\s*;\s*charset\s*=\s*utf-8)?$/i.test(contentType);
}

function hasSupportedEncoding(contentEncoding: string | null): boolean {
  return contentEncoding === null || /^identity$/i.test(contentEncoding);
}

async function readBody(request: Request): Promise<string | Response> {
  if (request.body === null) {
    return status(400);
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return status(413);
      }
      chunks.push(value);
    }
  } catch {
    return status(400);
  }

  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
  } catch {
    return status(400);
  }
}

function hasExpectedFields(value: Record<string, unknown>): boolean {
  const keys = Object.keys(value);
  return keys.length === ALLOWED_FIELDS.size && keys.every((key) => ALLOWED_FIELDS.has(key));
}

function isCompletedRecentUtcDay(period: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(period)) {
    return false;
  }
  const [year, month, day] = period.split("-").map(Number);
  const timestamp = Date.UTC(year, month - 1, day);
  if (new Date(timestamp).toISOString().slice(0, 10) !== period) {
    return false;
  }
  const now = new Date();
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return timestamp < today && timestamp >= today - 30 * DAY_MS;
}

function isRunCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= MAX_RUN_COUNT;
}

function parseUsageReport(raw: string): UsageReport | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }

  if (typeof value !== "object" || value === null || Array.isArray(value) || !hasExpectedFields(value as Record<string, unknown>)) {
    return null;
  }

  const report = value as Record<string, unknown>;
  if (
    report.schema_version !== 1 ||
    typeof report.installation_id !== "string" ||
    !UUID_V4.test(report.installation_id) ||
    typeof report.version !== "string" ||
    report.version.length > MAX_VERSION_LENGTH ||
    !SEMVER.test(report.version) ||
    typeof report.period !== "string" ||
    !isCompletedRecentUtcDay(report.period) ||
    !isRunCount(report.workflow_runs) ||
    !isRunCount(report.agent_runs) ||
    report.environment !== "production"
  ) {
    return null;
  }

  return {
    schema_version: 1,
    installation_id: report.installation_id.toLowerCase(),
    version: report.version,
    period: report.period,
    workflow_runs: report.workflow_runs,
    agent_runs: report.agent_runs,
    environment: "production",
  };
}

async function receiveUsage(request: Request, env: Env): Promise<Response> {
  if (new URL(request.url).search !== "") {
    return status(400);
  }
  if (!acceptsJson(request.headers.get("content-type")) || !hasSupportedEncoding(request.headers.get("content-encoding"))) {
    return status(415);
  }

  const raw = await readBody(request);
  if (raw instanceof Response) {
    return raw;
  }
  const report = parseUsageReport(raw);
  if (report === null) {
    return status(400);
  }

  try {
    await env.DB.prepare(
      `INSERT INTO usage_reports (
        installation_id, period, received_at, schema_version, version, workflow_runs, agent_runs
      ) VALUES (?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(installation_id, period) DO UPDATE SET
        received_at = excluded.received_at,
        schema_version = excluded.schema_version,
        version = excluded.version,
        workflow_runs = excluded.workflow_runs,
        agent_runs = excluded.agent_runs`,
    )
      .bind(
        report.installation_id,
        report.period,
        new Date().toISOString(),
        report.schema_version,
        report.version,
        report.workflow_runs,
        report.agent_runs,
      )
      .run();
  } catch {
    return status(503);
  }

  return new Response(null, { status: 204 });
}

export default {
  async fetch(request, env): Promise<Response> {
    const pathname = new URL(request.url).pathname;
    if (pathname === "/health") {
      return request.method === "GET" ? new Response("ok", { status: 200 }) : new Response(null, { status: 405 });
    }
    if (pathname === "/usage") {
      return request.method === "POST" ? receiveUsage(request, env) : new Response(null, { status: 405 });
    }
    return new Response(null, { status: 404 });
  },
} satisfies ExportedHandler<Env>;
