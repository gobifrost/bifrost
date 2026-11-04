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

describe("integration provider failures", () => {
	it.each([
		{ error: { detail: "Unavailable" } },
		{ data: { status: "Failed", result: [] } },
		{ data: { status: "Success", result: {} } },
	])(
		"rejects failed or invalid responses instead of returning an empty list",
		async (response) => {
			post.mockResolvedValue(response);
			await expect(
				getDataProviderOptions("provider-1"),
			).rejects.toThrow();
		},
	);
	it("preserves a successful empty list", async () => {
		post.mockResolvedValue({ data: { status: "Success", result: [] } });
		await expect(getDataProviderOptions("provider-1")).resolves.toEqual([]);
	});
	it("propagates transport failures to the query", async () => {
		post.mockRejectedValue(new Error("Network unavailable"));
		await expect(getDataProviderOptions("provider-1")).rejects.toThrow(
			"Network unavailable",
		);
	});
});

it("distinguishes failed option requests from a successful empty list", async () => {
	post.mockResolvedValueOnce({
		error: { detail: "Choice service unavailable" },
	});
	await expect(getFormFieldOptions("form-1", "owner")).rejects.toThrow(
		"Choice service unavailable",
	);
	post.mockRejectedValueOnce(new Error("Network unavailable"));
	await expect(getFormFieldOptions("form-1", "owner")).rejects.toThrow(
		"Network unavailable",
	);
	post.mockResolvedValueOnce({ data: { options: [] } });
	await expect(getFormFieldOptions("form-1", "owner")).resolves.toEqual([]);
});

it("loads field choices in the selected organization", async () => {
	post.mockResolvedValue({ data: { options: [] } });
	await getFormFieldOptions("form-1", "customer", {}, "contoso");
	expect(post).toHaveBeenCalledWith(
		"/api/forms/{form_id}/fields/{field_name}/options",
		{
			params: {
				path: { form_id: "form-1", field_name: "customer" },
				query: { org_id: "contoso" },
			},
			body: { inputs: {} },
		},
	);
});
