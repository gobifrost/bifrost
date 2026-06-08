import { describe, it, expect } from "vitest";

// The precedence rule main.tsx encodes (kept in sync with the scaffold in
// api/bifrost/commands/solution.py :: _v2_scaffold_files). Boot (deployed
// platform bootstrap) wins over the local VITE dev env, which wins over null.
function resolveAppId(boot: { appId?: string } | null | undefined, viteEnv: string | undefined): string | null {
  return boot?.appId ?? viteEnv ?? null;
}
function resolveOrg(boot: { orgScope?: string } | null | undefined, viteEnv: string | undefined): string | null {
  return boot?.orgScope ?? viteEnv ?? null;
}

describe("dev bootstrap precedence", () => {
  it("prefers the platform boot object when present (deployed)", () => {
    expect(resolveAppId({ appId: "DEPLOYED" }, "LOCAL")).toBe("DEPLOYED");
    expect(resolveOrg({ orgScope: "ORG_DEP" }, "ORG_LOCAL")).toBe("ORG_DEP");
  });
  it("falls back to VITE env when boot is absent (local dev)", () => {
    expect(resolveAppId(undefined, "LOCAL_APP")).toBe("LOCAL_APP");
    expect(resolveOrg(null, "LOCAL_ORG")).toBe("LOCAL_ORG");
  });
  it("is null when neither is present", () => {
    expect(resolveAppId(undefined, undefined)).toBeNull();
    expect(resolveOrg(undefined, undefined)).toBeNull();
  });
});
