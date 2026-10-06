import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import type { TraceNames } from "@/lib/access-trace";
import type { AccessTrace } from "@/services/access";

const motion = vi.hoisted(() => ({ reduced: false }));
vi.mock("framer-motion", async (importOriginal) => ({
	...(await importOriginal<typeof import("framer-motion")>()),
	useReducedMotion: () => motion.reduced,
}));

import { AccessTraceStrip } from "./AccessTraceStrip";

const names: TraceNames = {
	role: (id) => ({ "role-helpdesk": "Helpdesk" })[id],
	organization: (id) => ({ "org-1": "Contoso", "org-2": "Fabrikam" })[id],
	permission: (permission) =>
		({ "tables.readwrite": "Read and Write Tables" })[permission] ??
		permission,
};

const blocked: AccessTrace = {
	outcome: "failure",
	enforced: false,
	steps: [
		{
			key: "workflow_access",
			label: "Workflow access",
			status: "not_applicable",
			reason: "unattended",
			facts: {},
		},
		{
			key: "run_user",
			label: "Run user",
			status: "passed",
			reason: "identity",
			facts: { user_id: "identity-1", is_platform_admin: false },
		},
		{
			key: "powers",
			label: "Workflow powers",
			status: "passed",
			reason: "full",
			facts: { grants: [] },
		},
		{
			key: "target",
			label: "Target in reach",
			status: "stopped",
			reason: "outside",
			facts: { organization_id: "org-2" },
		},
		{
			key: "permission",
			label: "Permission",
			status: "not_reached",
			reason: "",
			facts: {},
		},
	],
};

const allowed: AccessTrace = {
	outcome: "success",
	enforced: false,
	steps: [
		{
			key: "run_user",
			label: "Run user",
			status: "passed",
			reason: "person",
			facts: { user_id: "user-1", is_platform_admin: false },
		},
		{
			key: "powers",
			label: "Workflow powers",
			status: "not_applicable",
			reason: "no_workflow",
			facts: {},
		},
		{
			key: "target",
			label: "Target in reach",
			status: "passed",
			reason: "role:role-helpdesk@organization",
			facts: { organization_id: "org-1" },
		},
		{
			key: "permission",
			label: "Permission",
			status: "passed",
			reason: "role:role-helpdesk:tables.readwrite@organization",
			facts: { permission: "tables.readwrite" },
		},
	],
};

function steps() {
	return within(
		screen.getByRole("list", { name: "Access Trace" }),
	).getAllByRole("listitem");
}

beforeEach(() => {
	motion.reduced = false;
});

describe("AccessTraceStrip", () => {
	it("shows each step in order, titled, with its status and sentence", () => {
		render(<AccessTraceStrip trace={allowed} names={names} />);

		expect(
			steps().map(
				(step) => within(step).getByRole("heading").textContent,
			),
		).toEqual([
			"Run User",
			"Workflow Powers",
			"Target in Reach",
			"Permission",
		]);
		expect(
			steps().map(
				(step) => within(step).getByTestId("step-status").textContent,
			),
		).toEqual(["Passed", "Not Applicable", "Passed", "Passed"]);
		expect(steps()[2]).toHaveTextContent("Helpdesk is placed on Contoso.");
		expect(steps()[3]).toHaveTextContent(
			"Helpdesk grants Read and Write Tables there.",
		);
	});

	it("marks the stopping step in amber and mutes the steps after it", () => {
		render(<AccessTraceStrip trace={blocked} names={names} />);

		const [, , , stopped, after] = steps();
		const stoppedStatus = within(stopped).getByTestId("step-status");
		expect(stoppedStatus).toHaveTextContent("Would Stop Here");
		expect(stoppedStatus).toHaveClass("text-[var(--bf-warning)]");
		expect(stopped).toHaveTextContent("Fabrikam is outside their reach.");
		expect(within(after).getByTestId("step-status")).toHaveTextContent(
			"Not Reached",
		);
		expect(after).toHaveClass("text-muted-foreground");
	});

	it("states the outcome as report-only", () => {
		const { rerender } = render(
			<AccessTraceStrip trace={allowed} names={names} />,
		);
		expect(screen.getByText("Would be allowed")).toBeInTheDocument();

		rerender(<AccessTraceStrip trace={blocked} names={names} />);
		expect(
			screen.getByText("Would be blocked — not enforced yet"),
		).toBeInTheDocument();
	});

	it("reveals the steps in sequence", () => {
		render(<AccessTraceStrip trace={allowed} names={names} />);

		expect(steps()[0].style.opacity).toBe("0");
	});

	it("shows every step at once under reduced motion", () => {
		motion.reduced = true;
		render(<AccessTraceStrip trace={allowed} names={names} />);

		for (const step of steps()) expect(step.style.opacity).not.toBe("0");
	});
});
