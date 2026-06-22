import { describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";

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

import { FilePolicyEditor } from "./FilePolicyEditor";

const BASE = {
	id: "pol-1",
	location: "workspace",
	path: "reports/",
	policies: { policies: [] },
} as const;

describe("FilePolicyEditor", () => {
	it("defaults to the YAML view", () => {
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={vi.fn()}
				onDelete={vi.fn()}
			/>,
		);
		// The YAML Monaco model (path "file-policies.yaml") is the active editor.
		expect(screen.getByLabelText("file-policies.yaml")).toBeInTheDocument();
	});

	it("blocks save while the document fails to parse", () => {
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: { value: "policies: [oops" },
		});
		expect(screen.getByText(/parse error/i)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: /save policy/i })).toBeDisabled();
		expect(onSave).not.toHaveBeenCalled();
	});

	it("saves the edited document with the location/path wrapper reattached", () => {
		const onSave = vi.fn();
		renderWithProviders(
			<FilePolicyEditor
				path="reports/june.txt"
				value={BASE}
				onSave={onSave}
				onDelete={vi.fn()}
			/>,
		);
		fireEvent.change(screen.getByLabelText("file-policies.yaml"), {
			target: {
				value:
					"policies:\n  - name: everyone_read\n    actions: [read, list]\n    when: null\n",
			},
		});
		fireEvent.click(screen.getByRole("button", { name: /save policy/i }));
		expect(onSave).toHaveBeenCalledTimes(1);
		const saved = onSave.mock.calls[0][0];
		expect(saved.location).toBe("workspace");
		expect(saved.path).toBe("reports/");
		expect(saved.id).toBe("pol-1");
		expect(saved.policies.policies[0].name).toBe("everyone_read");
	});
});
