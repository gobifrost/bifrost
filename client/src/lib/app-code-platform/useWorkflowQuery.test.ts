import { describe, expect, it } from "vitest";
import type { UseWorkflowMutationResult } from "./useWorkflowMutation";
import type { UseWorkflowQueryResult } from "./useWorkflowQuery";

// These tests exist to catch a specific historical regression: app code
// reaching for `error.message` because every other React-Query-shaped hook
// returns an Error object. Our `error` field is a plain string. We expose
// the new canonical name `errorMessage` (also a string) so the shape is
// self-documenting; `error` is kept as a deprecated alias.
//
// The tests are type-level + a tiny runtime shape assertion to guard the
// alias against accidental drift.

describe("useWorkflowQuery / useWorkflowMutation result shape", () => {
	it("exposes errorMessage as a string-or-null and keeps error as the same value", () => {
		const fakeMutation: UseWorkflowMutationResult<unknown> = {
			execute: async () => undefined,
			isLoading: false,
			isError: true,
			errorMessage: "boom",
			error: "boom",
			data: null,
			logs: [],
			reset: () => undefined,
			executionId: null,
			status: null,
		};

		expect(fakeMutation.errorMessage).toBe("boom");
		expect(fakeMutation.error).toBe(fakeMutation.errorMessage);
		expect(typeof fakeMutation.errorMessage).toBe("string");
	});

	it("Query result shape exposes the same alias", () => {
		const fakeQuery: UseWorkflowQueryResult<unknown> = {
			data: null,
			isLoading: false,
			isError: true,
			errorMessage: "kaboom",
			error: "kaboom",
			logs: [],
			refetch: async () => undefined,
			executionId: null,
			status: null,
		};

		expect(fakeQuery.errorMessage).toBe("kaboom");
		expect(fakeQuery.error).toBe(fakeQuery.errorMessage);
	});
});

// Type-level: assigning a non-string-or-null to errorMessage must fail to
// compile. The `void` here keeps this an unused-but-not-tree-shaken assertion.
type _ErrorMessageIsStringOrNull =
	UseWorkflowQueryResult<unknown>["errorMessage"] extends string | null
		? true
		: false;
const _check: _ErrorMessageIsStringOrNull = true;
void _check;
