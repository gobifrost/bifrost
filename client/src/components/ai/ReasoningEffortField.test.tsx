import { render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const { useProviderModels } = vi.hoisted(() => ({
	useProviderModels: vi.fn(),
}));

vi.mock("./ProviderModelField", () => ({ useProviderModels }));
vi.mock("@/components/ui/select", () => {
	const Pass = ({ children }: { children?: ReactNode }) => <>{children}</>;
	return {
		Select: Pass,
		SelectTrigger: Pass,
		SelectValue: () => null,
		SelectContent: Pass,
		SelectSeparator: () => null,
		SelectGroup: ({ children }: { children: ReactNode }) => (
			<div role="group">{children}</div>
		),
		SelectLabel: ({ children }: { children: ReactNode }) => (
			<div data-testid="group-label">{children}</div>
		),
		SelectItem: ({ children }: { children: ReactNode }) => (
			<div role="option">{children}</div>
		),
	};
});

import { ReasoningEffortField } from "./ReasoningEffortField";

function renderWithChoices(reasoning_choices: string[]) {
	useProviderModels.mockReturnValue({
		data: { models: [{ id: "m", display_name: "M", reasoning_choices }] },
	});
	return render(
		<ReasoningEffortField
			id="reasoning"
			connectionId="c"
			model="m"
			value={null}
			onValueChange={vi.fn()}
		/>,
	);
}

describe("ReasoningEffortField", () => {
	it("separates the on/off switch from effort levels", () => {
		renderWithChoices(["low", "high", "off", "on"]);

		const [toggle, effort] = screen.getAllByRole("group");
		expect(within(toggle).getByTestId("group-label")).toHaveTextContent("On / off");
		expect(within(toggle).getAllByRole("option").map((o) => o.textContent)).toEqual([
			"Off",
			"On (provider default)",
		]);
		expect(within(effort).getByTestId("group-label")).toHaveTextContent("Effort");
		expect(within(effort).getAllByRole("option").map((o) => o.textContent)).toEqual([
			"Low",
			"High",
		]);
	});

	it("omits a group the model has no choices for", () => {
		renderWithChoices(["low", "medium"]);

		expect(screen.getAllByRole("group")).toHaveLength(1);
		expect(screen.queryByText("On / off")).not.toBeInTheDocument();
	});

	it("renders nothing when the model lists no choices", () => {
		const { container } = renderWithChoices([]);
		expect(container).toBeEmptyDOMElement();
	});
});
