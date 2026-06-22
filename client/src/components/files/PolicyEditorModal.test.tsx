import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({
	listFilePolicies: vi.fn(),
	saveFilePolicy: vi.fn(),
	deleteFilePolicy: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// Monaco can't run in the test DOM — stub it to a textarea labelled by `path`.
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
import {
	listFilePolicies,
	saveFilePolicy,
} from "@/services/filePolicies";
import { PolicyEditorModal } from "./PolicyEditorModal";

describe("PolicyEditorModal", () => {
	beforeEach(() => {
		vi.mocked(listFilePolicies).mockReset();
		vi.mocked(saveFilePolicy).mockReset();
	});

	it("loads the best policy and saves edits with the right scope/location", async () => {
		vi.mocked(listFilePolicies).mockResolvedValue({
			policies: [
				{
					id: "p1",
					location: "gallery",
					path: "",
					organizationId: null,
					policies: { policies: [] },
				},
			],
		});
		vi.mocked(saveFilePolicy).mockResolvedValue({
			id: "p1",
			location: "gallery",
			path: "",
			organizationId: null,
			policies: { policies: [] },
		});
		const onSaved = vi.fn();
		render(
			<PolicyEditorModal
				open
				onOpenChange={vi.fn()}
				location="gallery"
				scope={null}
				path="pic.png"
				onSaved={onSaved}
			/>,
		);
		// Editor renders once the best policy resolves (YAML view by default).
		await waitFor(() =>
			expect(screen.getByLabelText("file-policies.yaml")).toBeInTheDocument(),
		);
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		await waitFor(() => expect(saveFilePolicy).toHaveBeenCalled());
		const saved = vi.mocked(saveFilePolicy).mock.calls[0][0];
		expect(saved.location).toBe("gallery");
		expect(saved.organizationId).toBeNull();
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});
});
