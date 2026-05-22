import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";

vi.mock("@monaco-editor/react", () => ({
	default: ({
		value,
		onChange,
		path,
	}: {
		value?: string;
		onChange?: (v: string | undefined) => void;
		path?: string;
	}) => (
		<textarea
			aria-label={path ?? "monaco-editor"}
			value={value ?? ""}
			onChange={(e) => onChange?.(e.target.value)}
		/>
	),
}));

vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

import { CustomClaimEditor } from "./CustomClaimEditor";
import type { CustomClaim } from "@/services/claims";

const claim: CustomClaim = {
	id: "11111111-1111-4111-8111-111111111111",
	organization_id: "22222222-2222-4222-8222-222222222222",
	name: "allowed_campus_ids",
	description: "",
	type: "list",
	query: { table: "user_campus_access", select: "campus_id" },
};

describe("CustomClaimEditor", () => {
	it("renders the claim fields", () => {
		renderWithProviders(
			<CustomClaimEditor
				value={claim}
				onChange={vi.fn()}
				onSave={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.getByDisplayValue("allowed_campus_ids")).toBeVisible();
		expect(screen.getByDisplayValue("list")).toBeVisible();
		expect(screen.getByLabelText("claim-query.json")).toHaveValue(
			JSON.stringify(claim.query, null, 2),
		);
	});

	it("disables Save when the query is invalid", () => {
		renderWithProviders(
			<CustomClaimEditor
				value={{ ...claim, query: null as unknown as CustomClaim["query"] }}
				onChange={vi.fn()}
				onSave={vi.fn()}
				onCancel={vi.fn()}
			/>,
		);

		expect(screen.getByRole("button", { name: /save/i })).toBeDisabled();
	});

	it("invokes onSave with the current value", () => {
		const onSave = vi.fn();
		renderWithProviders(
			<CustomClaimEditor
				value={claim}
				onChange={vi.fn()}
				onSave={onSave}
				onCancel={vi.fn()}
			/>,
		);

		fireEvent.click(screen.getByRole("button", { name: /save/i }));

		expect(onSave).toHaveBeenCalledWith(claim);
	});
});
