/**
 * Component tests for ExecutionResultPanel.
 *
 * The panel picks a renderer based on (resultType, typeof result).
 * We stub the two child renderers with distinctive text prefixes so the
 * dispatch logic is verifiable without relying on the real PrettyInputDisplay
 * or SafeHTMLRenderer internals (those have their own tests).
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

vi.mock("./PrettyInputDisplay", () => ({
	PrettyInputDisplay: ({
		inputData,
	}: {
		inputData: Record<string, unknown>;
	}) => (
		<div aria-label="pretty-input-stub">
			PRETTY:{JSON.stringify(inputData)}
		</div>
	),
}));

vi.mock("./SafeHTMLRenderer", () => ({
	SafeHTMLRenderer: ({ html, title }: { html: string; title?: string }) => (
		<div aria-label="safe-html-stub">
			HTML:{html}|TITLE:{title}
		</div>
	),
}));

async function renderPanel(
	props: Partial<
		Parameters<
			typeof import("./ExecutionResultPanel")["ExecutionResultPanel"]
		>[0]
	>,
) {
	const { ExecutionResultPanel } = await import("./ExecutionResultPanel");
	return renderWithProviders(<ExecutionResultPanel {...props} />);
}

describe("ExecutionResultPanel — empty/loading", () => {
	it("shows 'No result returned' when result is null", async () => {
		await renderPanel({ result: null });
		expect(screen.getByText(/no result returned/i)).toBeInTheDocument();
	});

	it("shows 'No result returned' when result is undefined", async () => {
		await renderPanel({ result: undefined });
		expect(screen.getByText(/no result returned/i)).toBeInTheDocument();
	});

	it("renders skeleton placeholders while loading and hides empty-state", async () => {
		const { container } = await renderPanel({
			result: null,
			isLoading: true,
		});
		// Skeleton component renders elements with animate-pulse; no semantic
		// role is exposed for skeletons, so this is the pragmatic assertion.
		expect(
			container.querySelectorAll(".animate-pulse").length,
		).toBeGreaterThan(0);
		expect(screen.queryByText(/no result returned/i)).not.toBeInTheDocument();
	});
});

describe("ExecutionResultPanel — renderer dispatch", () => {
	it("renders JSON objects via PrettyInputDisplay when resultType=json", async () => {
		await renderPanel({ result: { foo: "bar" }, resultType: "json" });
		const pretty = screen.getByLabelText("pretty-input-stub");
		expect(pretty.textContent).toContain('"foo":"bar"');
	});

	it("renders HTML strings via SafeHTMLRenderer when resultType=html", async () => {
		await renderPanel({
			result: "<p>hi</p>",
			resultType: "html",
			workflowName: "my_wf",
		});
		const html = screen.getByLabelText("safe-html-stub");
		expect(html.textContent).toContain("<p>hi</p>");
		expect(html.textContent).toContain("my_wf - Execution Result");
	});

	it("falls back to 'Execution Result' as HTML title when workflowName is absent", async () => {
		await renderPanel({ result: "<p>hi</p>", resultType: "html" });
		const html = screen.getByLabelText("safe-html-stub");
		expect(html.textContent).toContain("|TITLE:Execution Result");
	});

	it("renders text results as a <pre> block", async () => {
		await renderPanel({ result: "hello\nworld", resultType: "text" });
		const pre = screen.getByText(/hello/);
		expect(pre.tagName.toLowerCase()).toBe("pre");
		expect(pre.textContent).toBe("hello\nworld");
	});

	it("auto-detects objects when resultType is missing", async () => {
		await renderPanel({ result: { a: 1 } });
		const pretty = screen.getByLabelText("pretty-input-stub");
		expect(pretty.textContent).toContain('"a":1');
	});

	it("auto-detects strings when resultType is missing", async () => {
		await renderPanel({ result: "just a string" });
		const pre = screen.getByText("just a string");
		expect(pre.tagName.toLowerCase()).toBe("pre");
	});

	it("renders primitive numbers as stringified <pre>", async () => {
		await renderPanel({ result: 42 });
		const pre = screen.getByText("42");
		expect(pre.tagName.toLowerCase()).toBe("pre");
	});

	it("renders primitive booleans as stringified <pre>", async () => {
		await renderPanel({ result: true });
		const pre = screen.getByText("true");
		expect(pre.tagName.toLowerCase()).toBe("pre");
	});
});

describe("ExecutionResultPanel — section header", () => {
	it("renders the small-caps section label", async () => {
		await renderPanel({
			result: { ok: true },
			resultType: "json",
		});
		const heading = screen.getByRole("heading", { name: /result/i });
		expect(heading.tagName).toBe("H4");
		expect(screen.getByLabelText("pretty-input-stub")).toBeInTheDocument();
	});
});
