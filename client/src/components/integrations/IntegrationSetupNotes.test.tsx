import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	IntegrationSetupNotes,
	IntegrationSetupNotesContext,
	IntegrationSetupNotesDialog,
} from "./IntegrationSetupNotes";

vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		onChange,
		ariaLabel,
	}: {
		content: string;
		onChange: (value: string) => void;
		ariaLabel: string;
	}) => (
		<textarea
			aria-label={ariaLabel}
			value={content}
			onChange={(event) => onChange(event.target.value)}
		/>
	),
}));

describe("IntegrationSetupNotes", () => {
	it("retains the reference content when collapsed and expanded", async () => {
		const { user } = renderWithProviders(
			<IntegrationSetupNotes notes="## Connect the service" />,
		);
		const heading = screen.getByRole("heading", {
			name: "Connect the service",
		});
		await user.click(
			screen.getByRole("button", { name: "Collapse setup instructions" }),
		);
		expect(heading).toBeInTheDocument();
		expect(heading).not.toBeVisible();
		await user.click(
			screen.getByRole("button", { name: "Expand setup instructions" }),
		);
		expect(heading).toBeVisible();
	});

	it("saves content from the existing editor", async () => {
		const onSave = vi.fn().mockResolvedValue(undefined);
		const { user } = renderWithProviders(
			<IntegrationSetupNotesDialog
				open
				onOpenChange={vi.fn()}
				notes="Initial notes"
				onSave={onSave}
			/>,
		);
		const editor = await screen.findByRole("textbox", {
			name: "Setup instructions",
		});
		await user.clear(editor);
		await user.type(editor, "Updated notes");
		await user.click(
			screen.getByRole("button", { name: "Save Instructions" }),
		);
		expect(onSave).toHaveBeenCalledWith("Updated notes");
	});

	it.each(["  ## Keep indentation\n\nText  \n", "\n\n"])(
		"preserves nonempty source and clears blank notes: %j",
		async (notes) => {
			const onSave = vi.fn().mockResolvedValue(undefined);
			const { user } = renderWithProviders(
				<IntegrationSetupNotesDialog
					open
					onOpenChange={vi.fn()}
					notes={notes}
					onSave={onSave}
				/>,
			);
			await screen.findByRole("textbox", { name: "Setup instructions" });
			await user.click(
				screen.getByRole("button", { name: "Save Instructions" }),
			);
			expect(onSave).toHaveBeenCalledWith(notes.trim() ? notes : null);
		},
	);

	it("discards a cancelled draft when the editor is reopened", async () => {
		const onSave = vi.fn();
		const onOpenChange = vi.fn();
		const props = { onOpenChange, notes: "Saved notes", onSave };
		const { user, rerender } = renderWithProviders(
			<IntegrationSetupNotesDialog open {...props} />,
		);
		const editor = await screen.findByRole("textbox", {
			name: "Setup instructions",
		});
		await user.type(editor, " discarded");
		await user.click(screen.getByRole("button", { name: "Cancel" }));
		expect(onOpenChange).toHaveBeenCalledWith(false);
		expect(onSave).not.toHaveBeenCalled();
		rerender(<IntegrationSetupNotesDialog open={false} {...props} />);
		rerender(<IntegrationSetupNotesDialog open {...props} />);
		expect(
			await screen.findByRole("textbox", { name: "Setup instructions" }),
		).toHaveValue("Saved notes");
	});

	it("retains a failed save draft and reports recovery in the dialog", async () => {
		const onSave = vi.fn().mockRejectedValue(new Error("Save unavailable"));
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<IntegrationSetupNotesDialog
				open
				onOpenChange={onOpenChange}
				notes="Saved notes"
				onSave={onSave}
			/>,
		);
		const editor = await screen.findByRole("textbox", {
			name: "Setup instructions",
		});
		await user.clear(editor);
		await user.type(editor, "Unsaved runbook");
		await user.click(
			screen.getByRole("button", { name: "Save Instructions" }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Your draft is retained",
		);
		expect(editor).toHaveValue("Unsaved runbook");
		expect(onOpenChange).not.toHaveBeenCalled();
	});

	it("lets readers consult notes without exposing editing controls", () => {
		renderWithProviders(
			<IntegrationSetupNotes notes="## Read the runbook" />,
		);
		expect(
			screen.getByRole("heading", { name: "Read the runbook" }),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "Edit setup instructions" }),
		).not.toBeInTheDocument();
	});

	it("leaves empty read-only integrations without a reference panel", () => {
		renderWithProviders(
			<IntegrationSetupNotesContext>
				<input aria-label="Configuration value" />
			</IntegrationSetupNotesContext>,
		);
		expect(
			screen.getByRole("textbox", { name: "Configuration value" }),
		).toBeVisible();
		expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
		expect(
			screen.queryByRole("region", { name: "Setup instructions" }),
		).not.toBeInTheDocument();
	});
	it("preserves setup state when the reference is hidden and shown", async () => {
		const content = (
			<input aria-label="Configuration draft" defaultValue="Initial" />
		);
		const { user, rerender } = renderWithProviders(
			<IntegrationSetupNotesContext notes="Setup guide">
				{content}
			</IntegrationSetupNotesContext>,
		);
		await user.type(
			screen.getByRole("textbox", { name: "Configuration draft" }),
			" draft",
		);
		rerender(
			<IntegrationSetupNotesContext>
				{content}
			</IntegrationSetupNotesContext>,
		);
		expect(
			screen.getByRole("textbox", { name: "Configuration draft" }),
		).toHaveValue("Initial draft");
		rerender(
			<IntegrationSetupNotesContext notes="Setup guide">
				{content}
			</IntegrationSetupNotesContext>,
		);
		expect(
			screen.getByRole("textbox", { name: "Configuration draft" }),
		).toHaveValue("Initial draft");
	});
	it("opens runbook links separately from unfinished setup", () => {
		renderWithProviders(
			<IntegrationSetupNotes notes="Read the [setup guide](https://example.com/setup)." />,
		);
		const link = screen.getByRole("link", { name: "setup guide" });
		expect(link).toHaveAttribute("target", "_blank");
		expect(link).toHaveAttribute("rel", "noopener noreferrer");
	});
});
