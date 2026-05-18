import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EntityIdSourcePicker, type Candidate } from "./EntityIdSourcePicker";

const candidates: Candidate[] = [
	{ type: "id_token_claim", key: "tid", value: "tenant-abc" },
	{ type: "token_response_field", key: "team.id", value: "T123" },
];

describe("EntityIdSourcePicker", () => {
	it("renders all candidates with source / key / value", () => {
		render(
			<EntityIdSourcePicker
				candidates={candidates}
				onSelect={vi.fn()}
				onSkip={vi.fn()}
				isPending={false}
			/>,
		);
		expect(screen.getByText("id_token_claim")).toBeInTheDocument();
		expect(screen.getByText("tid")).toBeInTheDocument();
		expect(screen.getByText("tenant-abc")).toBeInTheDocument();
		expect(screen.getByText("token_response_field")).toBeInTheDocument();
		expect(screen.getByText("team.id")).toBeInTheDocument();
	});

	it("disables 'Use this field' until a row is selected", async () => {
		const user = userEvent.setup();
		render(
			<EntityIdSourcePicker
				candidates={candidates}
				onSelect={vi.fn()}
				onSkip={vi.fn()}
				isPending={false}
			/>,
		);
		const useBtn = screen.getByRole("button", { name: /use this field/i });
		expect(useBtn).toBeDisabled();

		await user.click(screen.getByText("tid"));
		expect(useBtn).toBeEnabled();
	});

	it("calls onSelect with the chosen candidate", async () => {
		const onSelect = vi.fn();
		const user = userEvent.setup();
		render(
			<EntityIdSourcePicker
				candidates={candidates}
				onSelect={onSelect}
				onSkip={vi.fn()}
				isPending={false}
			/>,
		);
		await user.click(screen.getByText("tid"));
		await user.click(screen.getByRole("button", { name: /use this field/i }));
		expect(onSelect).toHaveBeenCalledWith(candidates[0]);
	});

	it("calls onSkip when Skip is clicked", async () => {
		const onSkip = vi.fn();
		const user = userEvent.setup();
		render(
			<EntityIdSourcePicker
				candidates={candidates}
				onSelect={vi.fn()}
				onSkip={onSkip}
				isPending={false}
			/>,
		);
		await user.click(screen.getByRole("button", { name: /skip/i }));
		expect(onSkip).toHaveBeenCalled();
	});

	it("shows 'Saving…' on the Use button when isPending", () => {
		render(
			<EntityIdSourcePicker
				candidates={candidates}
				onSelect={vi.fn()}
				onSkip={vi.fn()}
				isPending={true}
			/>,
		);
		expect(
			screen.getByRole("button", { name: /saving/i }),
		).toBeInTheDocument();
	});
});
