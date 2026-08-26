import { useState } from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import {
	EventCriteriaForm,
	type EventCriteria,
	validateCriteria,
} from "./EventCriteriaForm";

function Harness({ initial = null }: { initial?: EventCriteria | null }) {
	const [value, setValue] = useState<EventCriteria | null>(initial);
	return (
		<>
			<EventCriteriaForm value={value} onChange={setValue} />
			<output data-testid="criteria-value">{JSON.stringify(value)}</output>
		</>
	);
}

describe("EventCriteriaForm", () => {
	it("starts unconditional and creates a bounded condition group", async () => {
		const { user } = renderWithProviders(<Harness />);

		expect(screen.getByText(/all events match/i)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /add criteria/i }));
		fireEvent.change(screen.getByLabelText("Criteria field"), {
			target: { value: "event.body.priority" },
		});
		fireEvent.change(screen.getByLabelText("Criteria value"), {
			target: { value: "high" },
		});

		expect(screen.getByTestId("criteria-value")).toHaveTextContent(
			'"field":"event.body.priority"',
		);
		expect(screen.getByTestId("criteria-value")).toHaveTextContent(
			'"value":"high"',
		);
	});

	it("supports nested groups without expression text", async () => {
		const { user } = renderWithProviders(<Harness />);
		await user.click(screen.getByRole("button", { name: /add criteria/i }));
		await user.click(screen.getByRole("button", { name: /^group$/i }));

		expect(screen.getAllByLabelText("Criteria field")).toHaveLength(2);
		expect(screen.getByTestId("criteria-value")).toHaveTextContent(
			'"kind":"all","items":[{"kind":"condition"',
		);
	});

	it("reports invalid paths and empty groups before submission", () => {
		const criteria: EventCriteria = {
			version: 1,
			root: {
				kind: "all",
				items: [
					{
						kind: "condition",
						field: "event.body.",
						operator: "equals",
						value: "high",
					},
				],
			},
		};

		expect(validateCriteria(criteria)).toContain(
			"Invalid criteria field: event.body.",
		);
		expect(
			validateCriteria({ version: 1, root: { kind: "all", items: [] } }),
		).toContain("Every criteria group must contain at least one condition");
	});
});
