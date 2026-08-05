import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: (...args: unknown[]) => post(...args) },
	$api: { useQuery: vi.fn() },
}));

import { getDataProviderOptions, getFormFieldOptions } from "./dataProviders";

describe("form provider services", () => {
	beforeEach(() => post.mockReset());

	it("names only the form field and sends evaluated inputs", async () => {
		post.mockResolvedValue({
			data: {
				options: [
					{
						value: "acme",
						label: "Acme",
						description: null,
						metadata: { email: "a@example.com" },
					},
				],
			},
			error: undefined,
		});

		await expect(
			getFormFieldOptions("form-1", "customer", { country: "US" }),
		).resolves.toEqual([
			{
				value: "acme",
				label: "Acme",
				metadata: { email: "a@example.com" },
			},
		]);
		expect(post).toHaveBeenCalledWith(
			"/api/forms/{form_id}/fields/{field_name}/options",
			{
				params: {
					path: { form_id: "form-1", field_name: "customer" },
				},
				body: { inputs: { country: "US" } },
			},
		);
	});

	it("retains direct provider execution for non-form admin surfaces", async () => {
		post.mockResolvedValue({
			data: {
				status: "Success",
				result: [{ value: "acme", label: "Acme" }],
			},
			error: undefined,
		});

		await expect(getDataProviderOptions("provider-1")).resolves.toEqual([
			{ value: "acme", label: "Acme" },
		]);
		expect(post).toHaveBeenCalledWith("/api/workflows/execute", {
			body: {
				workflow_id: "provider-1",
				input_data: {},
				transient: true,
			},
		});
	});
});
