import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import { ModelCapabilityEditor } from "./ModelCapabilityEditor";

describe("ModelCapabilityEditor", () => {
	beforeEach(() => authFetch.mockReset());

	it("runs provider verification for an unknown model and returns the result", async () => {
		authFetch.mockResolvedValue(
			new Response(
				JSON.stringify({
					capabilities: {
						image_input: true,
						pdf_input: true,
						tool_calling: true,
						native_image_output: false,
						source: "verified",
						fingerprint: "verified-target",
					},
					message: "Provider conformance check completed.",
				}),
				{ status: 200 },
			),
		);
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ModelCapabilityEditor
				provider="openai"
				model="private-model"
				endpoint="https://models.example.test/v1"
				apiKey="new-key"
				value={null}
				onChange={onChange}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: /verify with provider/i }),
		);

		await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
		expect(authFetch).toHaveBeenCalledWith(
			"/api/admin/llm/model-capabilities/verify",
			expect.objectContaining({
				method: "POST",
				body: expect.stringContaining('"api_key":"new-key"'),
			}),
		);
		expect(
			screen.getByText(/conformance check completed/i),
		).toBeInTheDocument();
	});

	it("records an administrator override as manual", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ModelCapabilityEditor
				provider="openai"
				model="private-model"
				endpoint=""
				value={null}
				onChange={onChange}
			/>,
		);

		await user.click(screen.getByRole("switch", { name: "Tool calling" }));
		expect(onChange).toHaveBeenCalledWith(
			expect.objectContaining({ tool_calling: true, source: "manual" }),
		);
	});
});
