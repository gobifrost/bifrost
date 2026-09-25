import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { ExecutionPreviewPanel } from "./ExecutionPreviewPanel";

vi.mock("@/pages/ExecutionDetails", () => ({
	ExecutionDetails: ({ executionId }: { executionId?: string }) => (
		<div>Embedded execution {executionId}</div>
	),
}));

describe("ExecutionPreviewPanel", () => {
	it("does not reserve preview space when no execution is selected", () => {
		const { container } = renderWithProviders(
			<ExecutionPreviewPanel
				executionId={null}
				onClose={vi.fn()}
				getOrigin={() => ({ href: "/history", scrollTop: 0 })}
			/>,
		);

		expect(
			screen.queryByRole("complementary", { name: "Execution preview" }),
		).not.toBeInTheDocument();
		expect(container).toBeEmptyDOMElement();
	});

	it("embeds execution details with close and open actions", async () => {
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<ExecutionPreviewPanel
				executionId="run-one"
				onClose={onClose}
				getOrigin={() => ({
					href: "/history?status=Failed&execution=run-one",
					scrollTop: 48,
				})}
			/>,
		);

		expect(screen.getByTestId("execution-preview-panel")).toHaveTextContent(
			"Embedded execution run-one",
		);
		expect(
			screen.getByRole("link", { name: "Open execution" }),
		).toHaveAttribute("href", "/history/run-one");

		await user.click(
			screen.getByRole("button", { name: "Close execution preview" }),
		);
		expect(onClose).toHaveBeenCalledTimes(1);
	});
});
