/**
 * Component tests for EntitySelector.
 *
 * Covers the four render branches: loading skeleton, error with retry,
 * empty-entities (all assigned) and normal combobox — plus verifying the
 * onChange signature (value + label).
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { EntitySelector } from "./EntitySelector";

describe("EntitySelector — loading", () => {
	it("renders a skeleton while loading", () => {
		renderWithProviders(
			<EntitySelector
				entities={[]}
				value=""
				onChange={() => {}}
				isLoading
			/>,
		);
		expect(
			screen.getByRole("status", { name: "Loading entities" }),
		).toBeInTheDocument();
	});
});

describe("EntitySelector — error", () => {
	it("renders an error message and fires onRetry when Retry is clicked", async () => {
		const onRetry = vi.fn();
		const { user } = renderWithProviders(
			<EntitySelector
				entities={[]}
				value=""
				onChange={() => {}}
				isError
				onRetry={onRetry}
			/>,
		);
		expect(screen.getByText(/error loading entities/i)).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /retry/i }));
		expect(onRetry).toHaveBeenCalledTimes(1);
	});
});

describe("EntitySelector — empty", () => {
	it("shows the 'all entities assigned' placeholder when no entities available", () => {
		renderWithProviders(
			<EntitySelector entities={[]} value="" onChange={() => {}} />,
		);
		// The trigger button renders the placeholder as the button label.
		expect(screen.getByText(/all entities assigned/i)).toBeInTheDocument();
	});
});

describe("EntitySelector — populated", () => {
	it("renders a combobox trigger showing the placeholder when nothing is selected", () => {
		renderWithProviders(
			<EntitySelector
				entities={[
					{ value: "ent-1", label: "Acme" },
					{ value: "ent-2", label: "Beta" },
				]}
				value=""
				onChange={() => {}}
			/>,
		);
		expect(
			screen.getByRole("combobox", { expanded: false }),
		).toBeInTheDocument();
		expect(screen.getByText(/select entity/i)).toBeInTheDocument();
	});

	it("surfaces the selected entity's label", () => {
		renderWithProviders(
			<EntitySelector
				entities={[
					{ value: "ent-1", label: "Acme" },
					{ value: "ent-2", label: "Beta" },
				]}
				value="ent-2"
				onChange={() => {}}
			/>,
		);
		expect(screen.getByText("Beta")).toBeInTheDocument();
	});

	it("clears the entity when the selected one is chosen again", async () => {
		const onChange = vi.fn();
		const { user } = renderWithProviders(
			<EntitySelector
				entities={[
					{ value: "ent-1", label: "Acme" },
					{ value: "ent-2", label: "Beta" },
				]}
				value="ent-2"
				onChange={onChange}
			/>,
		);
		await user.click(screen.getByRole("combobox"));
		await user.click(screen.getByRole("option", { name: "Beta" }));

		expect(onChange).toHaveBeenCalledWith("", "");
	});
});
