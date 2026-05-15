import { describe, expect, it } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { InfrastructureStatus } from "./InfrastructureStatus";

describe("InfrastructureStatus", () => {
	it("renders instance health, evidence, and novice explainers from the fixture", () => {
		renderWithProviders(<InfrastructureStatus />);

		expect(
			screen.getByRole("heading", { name: /Infrastructure Status/i }),
		).toBeInTheDocument();
		expect(screen.getByText("dev.bifrost.midtowntg.com")).toBeInTheDocument();
		expect(screen.getAllByText("Degraded")).toHaveLength(5);
		expect(screen.getAllByText("Limited impact")).toHaveLength(3);

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
});
