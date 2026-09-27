import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Integration } from "@/services/integrations";
import { IntegrationConnectionStatus } from "./IntegrationConnectionStatus";

const baseIntegration = {
	id: "review",
	name: "Review",
	has_oauth_config: true,
	is_deleted: false,
	created_at: "",
	updated_at: "",
} as Integration;

function integrationWith(statusCounts: Record<string, number>): Integration {
	return {
		...baseIntegration,
		connected_count:
			(statusCounts.completed ?? 0) + (statusCounts.connected ?? 0),
		needs_reconnection_count: statusCounts.failed ?? 0,
		connection_status_counts: statusCounts,
	};
}

describe("IntegrationConnectionStatus", () => {
	it.each([
		[{ completed: 2 }, "Connected"],
		[{ connected: 1 }, "Connected"],
		[{ completed: 2, failed: 1 }, "Degraded"],
		[{ failed: 2 }, "Failed"],
		[{}, "None"],
	] as const)("renders %s as %s", (counts, label) => {
		render(
			<IntegrationConnectionStatus
				integration={integrationWith(counts)}
			/>,
		);
		expect(
			screen.getByRole("button", {
				name: `OAuth connection health: ${label}`,
			}),
		).toBeInTheDocument();
	});

	it("opens a breakdown of connection statuses", async () => {
		const user = userEvent.setup();
		render(
			<IntegrationConnectionStatus
				integration={integrationWith({
					completed: 3,
					failed: 1,
					waiting_callback: 2,
				})}
			/>,
		);

		await user.click(
			screen.getByRole("button", {
				name: "OAuth connection health: Degraded",
			}),
		);

		const breakdown = screen.getByLabelText("OAuth connection breakdown");
		expect(
			within(breakdown).getByText("OAuth connections"),
		).toBeInTheDocument();
		expect(
			within(breakdown).getByText("3", { selector: "dd" }),
		).toBeInTheDocument();
		expect(
			within(breakdown).getByText("1", { selector: "dd" }),
		).toBeInTheDocument();
		expect(
			within(breakdown).getByText("2", { selector: "dd" }),
		).toBeInTheDocument();
	});

	it("does not make non-OAuth integrations interactive", () => {
		render(
			<IntegrationConnectionStatus
				integration={{ ...baseIntegration, has_oauth_config: false }}
			/>,
		);
		expect(screen.getByText("Not monitored")).toBeInTheDocument();
		expect(screen.queryByRole("button")).not.toBeInTheDocument();
	});
});
