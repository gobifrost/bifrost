import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { OPERATION_SUGGESTIONS, operationFor } from "./access-operations";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

interface AccessListEntry {
	method: string;
	path: string;
	operation_id: string | null;
	mcp_tool: string | null;
}

/** Every key the access check accepts: catalog ids and "METHOD /api/path". */
function accessListKeys(): Set<string> {
	const entries = JSON.parse(
		readFileSync(
			resolve(repoRoot, "docs/generated/access-list.json"),
			"utf8",
		),
	) as AccessListEntry[];
	return new Set(
		entries
			.filter((entry) => entry.mcp_tool === null)
			.flatMap((entry) => [
				`${entry.method} ${entry.path}`,
				...(entry.operation_id ? [entry.operation_id] : []),
			]),
	);
}

describe("OPERATION_SUGGESTIONS", () => {
	it("suggests only operations the access list names", () => {
		const keys = accessListKeys();

		expect(
			OPERATION_SUGGESTIONS.filter(
				({ operation }) => !keys.has(operation),
			),
		).toEqual([]);
	});

	it("labels each operation once", () => {
		const labels = OPERATION_SUGGESTIONS.map(({ label }) => label);

		expect(new Set(labels).size).toBe(labels.length);
	});
});

describe("operationFor", () => {
	it("reads a suggestion's name, in any case, as its operation", () => {
		expect(operationFor("Read Tables")).toBe("tables.list");
		expect(operationFor("  read tables ")).toBe("tables.list");
	});

	it("takes anything else as typed", () => {
		expect(operationFor(" GET /api/users ")).toBe("GET /api/users");
		expect(operationFor("agents.list")).toBe("agents.list");
	});
});
