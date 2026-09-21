import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { render, screen } from "@testing-library/react";

import { FleetHeader } from "./FleetHeader";

describe("FleetHeader", () => {
	it("links to the fleet Agent Workbench", () => {
		render(
			<MemoryRouter>
				<FleetHeader
					title="Agents"
					agentLabel="agent"
					total={2}
					active={2}
				/>
			</MemoryRouter>,
		);

		expect(
			screen.getByRole("link", { name: "Agent Workbench" }),
		).toHaveAttribute("href", "/agents/quality");
	});
});
