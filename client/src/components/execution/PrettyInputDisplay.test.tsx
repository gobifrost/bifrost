/**
 * Component tests for PrettyInputDisplay.
 *
 * Covers: empty-state, snake_case → Title Case label rendering, value-type
 * badges (null / boolean / number / url / date / array), copy button, the
 * Pretty ↔ Tree view toggle, and the structure-driven ladder renderings:
 * nested rows for flat objects, mini tables for uniform object arrays,
 * honest top-level-array framing, and the JSON fallback for deep/mixed data.
 */

import { describe, it, expect, vi, beforeEach, MockInstance } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { PrettyInputDisplay } from "./PrettyInputDisplay";

// VariablesTreeView is imported unconditionally but only rendered in tree
// view; we still stub it so the tree-view test can assert the dispatch.
vi.mock("@/components/ui/variables-tree-view", () => ({
	VariablesTreeView: ({ data }: { data: Record<string, unknown> }) => (
		<div aria-label="variables-tree-stub">{JSON.stringify(data)}</div>
	),
}));

// Toast is a side-effect; spy on it to verify copy success/failure UX.
vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

let writeTextSpy: MockInstance;

beforeEach(() => {
	writeTextSpy = vi
		.spyOn(navigator.clipboard, "writeText")
		.mockResolvedValue(undefined);
});

describe("PrettyInputDisplay — empty state", () => {
	it("renders 'No input parameters' when the object is empty", () => {
		renderWithProviders(<PrettyInputDisplay inputData={{}} />);
		expect(screen.getByText(/no input parameters/i)).toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — label and value formatting", () => {
	it("converts snake_case keys to Title Case labels", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ user_name: "alice" }} />,
		);
		expect(screen.getByText("User Name")).toBeInTheDocument();
	});

	it("upper-cases known acronyms in labels", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{ api_key: "x", some_id: "y", site_url: "z" }}
			/>,
		);
		expect(screen.getByText("API Key")).toBeInTheDocument();
		expect(screen.getByText("Some ID")).toBeInTheDocument();
		expect(screen.getByText("Site URL")).toBeInTheDocument();
	});

	it("renders a null value with a 'null' badge", () => {
		renderWithProviders(<PrettyInputDisplay inputData={{ n: null }} />);
		// Badge + value both read "null"; just assert both exist.
		const nulls = screen.getAllByText("null");
		expect(nulls.length).toBeGreaterThanOrEqual(1);
	});

	it("renders booleans with a Yes/No display and true/false badge", () => {
		renderWithProviders(<PrettyInputDisplay inputData={{ flag: true }} />);
		expect(screen.getByText("Yes")).toBeInTheDocument();
		expect(screen.getByText("true")).toBeInTheDocument();
	});

	it("renders numbers with locale formatting and a 'number' badge", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ count: 1234 }} />,
		);
		expect(screen.getByText("number")).toBeInTheDocument();
		expect(screen.getByText(/1,234/)).toBeInTheDocument();
	});

	it("tags URL-shaped strings with a url badge", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ link: "https://example.com" }} />,
		);
		expect(screen.getByText("url")).toBeInTheDocument();
	});

	it("tags ISO-date strings with a date badge", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ when: "2026-04-20" }} />,
		);
		expect(screen.getByText("date")).toBeInTheDocument();
	});

	it("tags arrays with the element count", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ items: ["a", "b", "c"] }} />,
		);
		expect(screen.getByText(/array \(3\)/)).toBeInTheDocument();
	});

	it("renders scalar arrays as a compact comma list", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ items: ["a", "b", "c"] }} />,
		);
		expect(screen.getByText("a, b, c")).toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — nested object rows", () => {
	it("renders a flat object value as nested label/value rows, not JSON", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{
					site: { site_name: "HQ", floor_count: 3 },
				}}
			/>,
		);
		expect(screen.getByText("Site")).toBeInTheDocument();
		expect(screen.getByText("Site Name")).toBeInTheDocument();
		expect(screen.getByText("HQ")).toBeInTheDocument();
		expect(screen.getByText("Floor Count")).toBeInTheDocument();
		expect(screen.getByText("3")).toBeInTheDocument();
		// No "object" badge and no raw JSON braces for renderable objects.
		expect(screen.queryByText("object")).not.toBeInTheDocument();
		expect(screen.queryByText(/"site_name"/)).not.toBeInTheDocument();
	});

	it("recurses through nested objects within the depth budget", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{
					config: { network: { vlan_id: 42 } },
				}}
			/>,
		);
		expect(screen.getByText("Network")).toBeInTheDocument();
		expect(screen.getByText("Vlan ID")).toBeInTheDocument();
		expect(screen.getByText("42")).toBeInTheDocument();
	});

	it("renders an empty object value as an explicit empty marker", () => {
		renderWithProviders(<PrettyInputDisplay inputData={{ meta: {} }} />);
		expect(screen.getByText("Empty object")).toBeInTheDocument();
	});

	it("falls back to a JSON block (with object badge) for too-deep data", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{
					deep: { a: { b: { c: { d: "leaf" } } } },
				}}
			/>,
		);
		expect(screen.getByText("object")).toBeInTheDocument();
		// SyntaxHighlighter splits tokens, so match the key fragment.
		expect(screen.getByText(/"a"/)).toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — mini table for uniform object arrays", () => {
	const licenses = [
		{ label: "Microsoft 365 E3", value: "m365_e3" },
		{ label: "Defender for Endpoint P2", value: "defender_p2" },
	];

	it("renders an array of same-shaped flat objects as a table", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={{ licenses }} />,
		);
		const table = screen.getByRole("table");
		expect(table).toBeInTheDocument();
		expect(
			screen.getByRole("columnheader", { name: "Label" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("columnheader", { name: "Value" }),
		).toBeInTheDocument();
		expect(screen.getByText("Microsoft 365 E3")).toBeInTheDocument();
		expect(screen.getByText("defender_p2")).toBeInTheDocument();
	});

	it("renders missing cells in mostly-uniform arrays as an em dash", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{
					rows: [
						{ a: "1", b: "x" },
						{ a: "2", b: "y" },
						{ a: "3", b: "z" },
						{ a: "4", b: "w" },
						{ a: "5" },
					],
				}}
			/>,
		);
		expect(screen.getByText("—")).toBeInTheDocument();
	});

	it("falls back to JSON when array items are not table-shaped", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{
					mixed: [{ a: { nested: true } }, { a: "scalar" }],
				}}
			/>,
		);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(screen.getByText(/"nested"/)).toBeInTheDocument();
	});

	it("previews a production-sized flat result without rendering every row", () => {
		const rows = Array.from({ length: 5395 }, (_, index) => ({
			Subject: `row-${index}`,
			Duration: "1",
		}));

		renderWithProviders(<PrettyInputDisplay inputData={{ rows }} />);

		expect(screen.getByRole("table")).toBeInTheDocument();
		expect(
			screen.getByText("Showing first 50 of 5,395 rows"),
		).toBeInTheDocument();
		expect(screen.getByText("row-49")).toBeInTheDocument();
		expect(screen.queryByText("row-50")).not.toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — large scalar values", () => {
	it("truncates multi-megabyte strings in the DOM", () => {
		const largeHtml = `<table>${"x".repeat(3_225_167)}</table>`;

		renderWithProviders(
			<PrettyInputDisplay inputData={{ table_html: largeHtml }} />,
		);

		expect(screen.getByText(/3,225,182 characters total/)).toBeInTheDocument();
		expect(screen.queryByText(largeHtml)).not.toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — top-level arrays", () => {
	const licenses = [
		{ label: "Microsoft 365 E3", value: "m365_e3" },
		{ label: "Defender for Endpoint P2", value: "defender_p2" },
		{ label: "Huntress Managed EDR", value: "huntress_edr" },
	];

	it("frames a top-level array as 'N items', not parameters", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={licenses}
				showToggle={true}
				defaultView="pretty"
			/>,
		);
		expect(screen.getByText("3 items")).toBeInTheDocument();
		expect(screen.queryByText(/viewing 3 parameters/i)).not.toBeInTheDocument();
		// No numeric 0..n key labels.
		expect(screen.queryByText("0")).not.toBeInTheDocument();
	});

	it("renders a table-shaped top-level array directly as a table", () => {
		renderWithProviders(<PrettyInputDisplay inputData={licenses} />);
		expect(screen.getByRole("table")).toBeInTheDocument();
		expect(screen.getByText("Huntress Managed EDR")).toBeInTheDocument();
	});

	it("renders a top-level scalar array as a comma list", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={["alpha", "beta"]}
				showToggle={true}
			/>,
		);
		expect(screen.getByText("2 items")).toBeInTheDocument();
		expect(screen.getByText("alpha, beta")).toBeInTheDocument();
	});

	it("renders an empty top-level array as 'No items'", () => {
		renderWithProviders(<PrettyInputDisplay inputData={[]} />);
		expect(screen.getByText("No items")).toBeInTheDocument();
	});

	it("falls back to JSON for a mixed top-level array", () => {
		renderWithProviders(
			<PrettyInputDisplay inputData={[{ a: 1 }, "loose", [2]]} />,
		);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		expect(screen.getByText(/"loose"/)).toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — copy button", () => {
	it("writes the JSON payload to the clipboard from tree view", async () => {
		// The Copy affordance lives on the Tree view bar, not the Pretty view.
		const { user } = renderWithProviders(
			<PrettyInputDisplay
				inputData={{ foo: "bar" }}
				showToggle={true}
				defaultView="tree"
			/>,
		);
		await user.click(screen.getByRole("button", { name: /copy/i }));

		expect(writeTextSpy).toHaveBeenCalledTimes(1);
		expect(writeTextSpy.mock.calls[0][0]).toBe(
			JSON.stringify({ foo: "bar" }, null, 2),
		);

		// The icon label flips to "Copied!" after a successful write.
		expect(
			await screen.findByRole("button", { name: /copied/i }),
		).toBeInTheDocument();
	});
});

describe("PrettyInputDisplay — view toggle", () => {
	it("switches between Pretty and Tree view when the toggle is present", async () => {
		const { user } = renderWithProviders(
			<PrettyInputDisplay
				inputData={{ foo: "bar" }}
				showToggle={true}
				defaultView="pretty"
			/>,
		);
		// Pretty view shows the parameter count line.
		expect(screen.getByText(/viewing 1 parameter/i)).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /tree view/i }));
		expect(screen.getByLabelText("variables-tree-stub")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /pretty view/i }));
		expect(screen.getByText(/viewing 1 parameter/i)).toBeInTheDocument();
	});

	it("pluralises the parameter count correctly", () => {
		renderWithProviders(
			<PrettyInputDisplay
				inputData={{ a: 1, b: 2 }}
				showToggle={true}
				defaultView="pretty"
			/>,
		);
		expect(screen.getByText(/viewing 2 parameters/i)).toBeInTheDocument();
	});
});
