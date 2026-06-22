import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

// Monaco → textarea labelled by `path`.
vi.mock("@monaco-editor/react", () => ({
	default: ({ value, path }: { value?: string; path?: string }) => (
		<textarea aria-label={path ?? "monaco-editor"} value={value ?? ""} readOnly />
	),
}));
vi.mock("@/contexts/ThemeContext", () => ({
	useTheme: () => ({ theme: "light" }),
}));

import { PolicyExampleBlock } from "./PolicyExampleBlock";

const POLICY = { policies: [{ name: "admin_bypass", actions: ["read"] }] };

describe("PolicyExampleBlock", () => {
	it("defaults to YAML and toggles to JSON", () => {
		render(
			<PolicyExampleBlock
				heading="admin_bypass"
				description="desc"
				policy={POLICY}
				index={0}
			/>,
		);
		// YAML is the default view.
		const yamlEditor = screen.getByLabelText("example-0.yaml") as HTMLTextAreaElement;
		expect(yamlEditor).toBeInTheDocument();
		expect(yamlEditor.value).toMatch(/policies:/);
		expect(yamlEditor.value).not.toMatch(/^\{/);

		fireEvent.click(screen.getByRole("button", { name: /^json$/i }));
		const jsonEditor = screen.getByLabelText("example-0.json") as HTMLTextAreaElement;
		expect(JSON.parse(jsonEditor.value)).toHaveProperty("policies");
	});

	it("copies the currently-shown format", () => {
		const writeText = vi.fn();
		Object.defineProperty(navigator, "clipboard", {
			value: { writeText },
			configurable: true,
		});
		render(
			<PolicyExampleBlock
				heading="admin_bypass"
				description="desc"
				policy={POLICY}
				index={1}
			/>,
		);
		fireEvent.click(screen.getByRole("button", { name: /^copy$/i }));
		expect(writeText).toHaveBeenCalledWith(expect.stringContaining("policies:"));
	});
});
