import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import { FilePolicyEditor } from "./FilePolicyEditor";

describe("FilePolicyEditor", () => {
	it("blocks save when the JSON policy document is invalid", async () => {
		const onSave = vi.fn();
		const { user } = renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={{
					id: "pol-1",
					location: "workspace",
					path: "reports/",
					policies: { policies: [] },
				}}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);

		await user.clear(screen.getByLabelText(/policy json/i));
		fireEvent.change(screen.getByLabelText(/policy json/i), {
			target: { value: "{" },
		});

		expect(screen.getByText(/invalid json/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /save policy/i })).toBeDisabled();
		expect(onSave).not.toHaveBeenCalled();
	});

	it("saves the parsed policy document", async () => {
		const onSave = vi.fn();
		const { user } = renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={{
					id: "pol-1",
					location: "workspace",
					path: "reports/",
					policies: { policies: [] },
				}}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);

		await user.click(screen.getByRole("button", { name: /save policy/i }));

		expect(onSave).toHaveBeenCalledWith({
			id: "pol-1",
			location: "workspace",
			path: "reports/",
			policies: { policies: [] },
		});
	});
});
