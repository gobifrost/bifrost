/**
 * Tripwire: the SDK's wire-facing surface (wire-surface.ts) is snapshot-hashed
 * so any change to it forces a conscious decision, mirroring the CLI's
 * DTO-fingerprint tripwire (api/tests/unit/test_contract_version.py).
 *
 * On mismatch: refresh SNAPSHOT_HASH below if the change is non-breaking
 * (e.g. a new optional field/endpoint an old SDK simply never called);
 * bump sdk-contract.json's "version" (with a history entry explaining why)
 * if it's breaking.
 */
import { describe, expect, it } from "vitest";

import contract from "./sdk-contract.json";
import { wireSurface } from "./wire-surface";

// Simple, dependency-free stable hash (FNV-1a) over the sorted-key JSON
// serialization of wireSurface. Not cryptographic — just needs to change
// whenever the object's shape changes.
function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>).sort();
    return `{${keys
      .map((k) => `${JSON.stringify(k)}:${stableStringify((value as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function fnv1aHex(input: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

const SNAPSHOT_HASH = "bcffced8";

describe("SDK wire-surface tripwire", () => {
  it("matches the committed snapshot hash", () => {
    const hash = fnv1aHex(stableStringify(wireSurface));
    expect(
      hash,
      `wire-surface.ts hash changed (${hash} !== ${SNAPSHOT_HASH}). ` +
        "Refresh SNAPSHOT_HASH in sdk-contract.test.ts if this is a non-breaking " +
        "change; bump sdk-contract.json's \"version\" (with a history entry) if " +
        "it's a breaking change to the SDK<->server wire contract.",
    ).toBe(SNAPSHOT_HASH);
  });

  it("sdk-contract.json has a positive integer version", () => {
    expect(typeof contract.version).toBe("number");
    expect(Number.isInteger(contract.version)).toBe(true);
    expect(contract.version).toBeGreaterThan(0);
  });
});
