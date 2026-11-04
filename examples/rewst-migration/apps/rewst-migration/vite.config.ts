import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Tokenless local dev follows the normal CLI profile selection:
//   1. process env (set by `bifrost solution start`), then
//   2. BIFROST_API_URL from .env in this exact invocation directory, then
//   3. the CLI's selected default profile.
// Deployed, the platform passes these values to the app's mount() lifecycle.
function readBifrostEnv() {
  const out = {
    url: process.env.BIFROST_API_URL || "",
    token: process.env.BIFROST_ACCESS_TOKEN || "",
  };
  const envPath = join(process.cwd(), ".env");
  if (existsSync(envPath)) {
    for (const line of readFileSync(envPath, "utf8").split("\n")) {
      const m = line.match(/^\s*BIFROST_API_URL\s*=\s*(.*)\s*$/);
      if (m) {
        const v = m[1].replace(/^["']|["']$/g, "");
        if (!out.url) out.url = v;
      }
    }
  }
  // Fall back to the selected CLI profile, or credentials keyed by the
  // explicit URL above when one is present.
  if (!out.token) {
    try {
      const args = ["auth", "token"];
      if (out.url) args.push("--url", out.url);
      const raw = execFileSync("bifrost", args, {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "ignore"],
      });
      const creds = JSON.parse(raw);
      if (creds.access_token) out.token = creds.access_token;
      if (creds.api_url && !out.url) out.url = creds.api_url;
    } catch {
      // CLI absent / not logged in — leave tokenless; main.tsx surfaces the
      // unauthenticated state rather than crashing the dev server.
    }
  }
  return out;
}

export default defineConfig(({ command }) => {
  const env = readBifrostEnv();
  // SECURITY: the dev token is injected ONLY for `vite` (serve / `npm run dev`),
  // never for `vite build`. Baking BIFROST_ACCESS_TOKEN into the production
  // bundle via `define` would ship a usable credential to every app user
  // (Codex R6-P1-c). In a deployed build the token comes from the platform's
  // per-mount bootstrap at runtime; the bundle stays tokenless.
  const define =
    command === "serve"
      ? {
          "import.meta.env.VITE_BIFROST_API_URL": JSON.stringify(env.url),
          "import.meta.env.VITE_BIFROST_TOKEN": JSON.stringify(env.token),
          "import.meta.env.VITE_BIFROST_APP_ID": JSON.stringify(process.env.VITE_BIFROST_APP_ID || ""),
          "import.meta.env.VITE_BIFROST_ORG_ID": JSON.stringify(process.env.VITE_BIFROST_ORG_ID || null),
        }
      : {};
  return {
    plugins: [react(), tailwindcss()],
    define,
    // `@/` → src, so shadcn component source (which imports `@/lib/utils` and
    // `@/components/ui/*`) resolves the same as in the shadcn docs.
    resolve: { alias: { "@": join(process.cwd(), "src") } },
  };
});
