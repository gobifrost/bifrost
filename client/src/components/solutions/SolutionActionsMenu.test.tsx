import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SolutionActionsMenu } from "./SolutionActionsMenu";

const defaultProps = {
	exporting: false,
	onCapture: vi.fn(),
	onExport: vi.fn(),
	onEdit: vi.fn(),
	onDelete: vi.fn(),
};

describe("SolutionActionsMenu", () => {
	it("labels the export action 'Export Solution'", async () => {
		render(<SolutionActionsMenu {...defaultProps} />);
		await userEvent.click(screen.getByTestId("solution-actions"));
		expect(
			screen.getByTestId("export-solution"),
		).toHaveTextContent("Export Solution");
	});
});
