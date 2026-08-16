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

		await user.click(
			screen.getByRole("button", {
				name: "Tool Calling: Not Verified",
			}),
		);
		expect(onChange).toHaveBeenCalledWith(
			expect.objectContaining({ tool_calling: true, source: "manual" }),
		);
	});

	it("shows supported and unsupported capabilities as compact icon controls", async () => {
		const { user } = renderWithProviders(
			<ModelCapabilityEditor
				provider="openai"
				model="deepseek/deepseek-v4-pro"
				endpoint="https://openrouter.ai/api/v1"
				value={{
					image_input: false,
					pdf_input: false,
					tool_calling: true,
					source: "openrouter",
					fingerprint: "catalog-target",
				}}
				onChange={vi.fn()}
			/>,
		);

		expect(
			screen.getByRole("button", {
				name: "Image Input: Not Supported",
			}),
		).toHaveClass("text-red-600");
		expect(
			screen.getByRole("button", {
				name: "Tool Calling: Supported",
			}),
		).toHaveClass("text-green-600");
		expect(screen.queryByRole("switch")).not.toBeInTheDocument();
		expect(
			screen.queryByText(/native image generation/i),
		).not.toBeInTheDocument();

		await user.hover(
			screen.getByRole("button", {
				name: "Tool Calling: Supported",
			}),
		);
		expect(
			await screen.findByText("Supported · OpenRouter Catalog"),
		).toBeInTheDocument();
	});
});
