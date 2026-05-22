import { afterEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { InfrastructureStatus } from "./InfrastructureStatus";

describe("InfrastructureStatus", () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("renders instance health, evidence, and novice explainers from the fixture", () => {
		vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));

		renderWithProviders(<InfrastructureStatus />);

		expect(
			screen.getByRole("heading", { name: /Infrastructure Status/i }),
		).toBeInTheDocument();
		expect(screen.getByText("dev.bifrost.midtowntg.com")).toBeInTheDocument();
		expect(screen.getAllByText("Degraded")).toHaveLength(5);
		expect(screen.getAllByText("Limited impact")).toHaveLength(3);
		expect(screen.getByText("Fallback snapshot")).toBeInTheDocument();

		const apiNode = screen.getByRole("article", {
			name: /API readiness Healthy/i,
		});
		expect(within(apiNode).getByText("/health/ready")).toBeInTheDocument();

		const executionNode = screen.getByRole("article", {
			name: /Execution plane Degraded/i,
		});
		expect(
			within(executionNode).getByText(/Open History/i),
		).toBeInTheDocument();

		expect(
			screen.getByText(/API readiness proves the API can reach/i),
		).toBeInTheDocument();
		expect(
			screen.getByText(/External integrations are third-party systems/i),
		).toBeInTheDocument();
	});

	it("keeps external integrations advisory in the instance rollup", () => {
		vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));

		renderWithProviders(<InfrastructureStatus />);

		const externalNode = screen.getByRole("article", {
			name: /External integrations Advisory/i,
		});

		expect(within(externalNode).getByText("Advisory")).toBeInTheDocument();
		expect(within(externalNode).getByText("None impact")).toBeInTheDocument();
		expect(
			screen.getByText(/advisory unless tied to active work/i),
		).toBeInTheDocument();
	});

	it("renders live infrastructure status when the endpoint responds", async () => {
		vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response(
				JSON.stringify({
					environment: "poc",
					instance: "dev.bifrost.midtowntg.com",
					generated_at: "2026-05-22T15:25:44.479073Z",
					status: "Healthy",
					impact: "None",
					nodes: [
						{
							id: "api-readiness",
							label: "API readiness",
							domain: "API Readiness",
							status: "Healthy",
							impact: "None",
							summary: "Live API status loaded.",
							explainer: "Live endpoint data replaced the baked fallback.",
							evidence: {
								source: "/health/ready",
								sampled_at: "2026-05-22T15:25:44.479073Z",
								freshness: "fresh",
							},
							links: [],
						},
					],
					edges: [],
				}),
				{ headers: { "Content-Type": "application/json" }, status: 200 },
			),
		);

		renderWithProviders(<InfrastructureStatus />);

		expect(await screen.findByText("Live feed")).toBeInTheDocument();
		expect(screen.getByText("Live API status loaded.")).toBeInTheDocument();
		expect(screen.queryByText("Fallback snapshot")).not.toBeInTheDocument();
		expect(globalThis.fetch).toHaveBeenCalledWith(
			"/infrastructure/status.json",
			{
				cache: "no-store",
				headers: { Accept: "application/json" },
			},
		);
	});

	it("keeps the fallback visible when the endpoint is unavailable", async () => {
		vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response("not found", { status: 404 }),
		);

		renderWithProviders(<InfrastructureStatus />);

		expect(await screen.findByText("Fallback snapshot")).toBeInTheDocument();
		expect(screen.getByText("Using fallback snapshot")).toBeInTheDocument();
		expect(
			screen.getByText(/status endpoint returned 404/i),
		).toBeInTheDocument();
	});
});
