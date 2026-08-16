import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const authFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ authFetch }));

import { ModelCapabilityEditor } from "./ModelCapabilityEditor";

describe("ModelCapabilityEditor", () => {
	beforeEach(() => {
		authFetch.mockReset();
	});

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
		const capabilities = {
			image_input: false,
			pdf_input: false,
			tool_calling: true,
			source: "openrouter" as const,
			fingerprint: "catalog-target",
		};
		const { user } = renderWithProviders(
			<ModelCapabilityEditor
				provider="openai"
				model="deepseek/deepseek-v4-pro"
				endpoint="https://openrouter.ai/api/v1"
				value={capabilities}
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
		expect(
			screen.getByRole("button", {
				name: "Tool Calling: Supported",
			}),
		).toHaveClass("h-6", "w-6");
		expect(
			screen
				.getByRole("button", {
					name: "Tool Calling: Supported",
				})
				.querySelector("svg"),
		).toHaveClass("h-3.5", "w-3.5");
		expect(screen.queryByRole("switch")).not.toBeInTheDocument();
		expect(screen.queryByText("Capabilities")).not.toBeInTheDocument();
		expect(screen.getByText("OpenRouter")).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "Refresh Model Capabilities",
			}),
		).toContainElement(screen.getByText("OpenRouter"));
		expect(
			screen.queryByText(/native image generation/i),
		).not.toBeInTheDocument();

		await user.hover(
			screen.getByRole("button", {
				name: "Tool Calling: Supported",
			}),
		);
		expect(
			await screen.findByText("Supported · OpenRouter"),
		).toBeInTheDocument();
	});

	it("refreshes through the source status and replaces its check with a spinner", async () => {
		let resolveLookup: ((response: Response) => void) | undefined;
		authFetch.mockImplementation(
			() =>
				new Promise<Response>((resolve) => {
					resolveLookup = resolve;
				}),
		);
		const capabilities = {
			image_input: false,
			pdf_input: false,
			tool_calling: true,
			source: "openrouter" as const,
			fingerprint: "catalog-target",
		};
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<ModelCapabilityEditor
				provider="openai"
				model="deepseek/deepseek-v4-pro"
				endpoint="https://openrouter.ai/api/v1"
				value={capabilities}
				onChange={onChange}
			/>,
		);
		const refresh = screen.getByRole("button", {
			name: "Refresh Model Capabilities",
		});

		await user.click(refresh);
		expect(refresh.querySelector(".animate-spin")).toBeInTheDocument();
		expect(refresh).toHaveTextContent("OpenRouter");

		resolveLookup?.(
			new Response(
				JSON.stringify({
					capabilities,
					message: "Catalog refreshed.",
				}),
				{ status: 200 },
			),
		);
		await waitFor(() =>
			expect(onChange).toHaveBeenCalledWith(capabilities),
		);
	});
});
