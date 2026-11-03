import { describe, expect, it } from "vitest";

import { connectionState, migrationProgress } from "./migrationPresentation";

describe("connectionState", () => {
  it("keeps an unconfigured installation focused on configuration", () => {
    expect(
      connectionState({
        api_url: null,
        root_org_id: null,
        cookie_configured: false,
      }),
    ).toEqual({
      label: "Setup required",
      tone: "warning",
      action: "Configure connection",
    });
  });

  it("does not call stored credentials verified until the live check succeeds", () => {
    expect(
      connectionState({
        api_url: "https://api.rewst.io/graphql",
        root_org_id: "org-1",
        cookie_configured: true,
      }),
    ).toEqual({
      label: "Connection not verified",
      tone: "warning",
      action: "Verify connection",
    });
  });
});

describe("migrationProgress", () => {
  it("reports tested items separately from planned work", () => {
    expect(
      migrationProgress([
        { status: "discovered" },
        { status: "planned" },
        { status: "implementing" },
        { status: "tested" },
      ]),
    ).toEqual({ total: 4, tested: 1, remaining: 3 });
  });
});
