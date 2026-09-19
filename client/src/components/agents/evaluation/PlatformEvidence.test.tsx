import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
	EvidenceJson,
	PlatformError,
	PlatformStatus,
} from "./PlatformEvidence";
describe("platform evidence", () => {
	it("names a recovery state without relying on color", () => {
		render(<PlatformStatus status="recovery_required" />);
		expect(screen.getByText("recovery required")).toBeVisible();
	});
	it("keeps evidence keyboard-scrollable and errors actionable", () => {
		const retry = vi.fn();
		render(
			<>
				<EvidenceJson label="Tool result" value={{ empty: null }} />
				<PlatformError
					error={new Error("Connection lost")}
					retry={retry}
				/>
			</>,
		);
		expect(screen.getByLabelText("Tool result")).toHaveAttribute(
			"tabindex",
			"0",
		);
		expect(screen.getByRole("alert")).toHaveTextContent("Connection lost");
		fireEvent.click(screen.getByRole("button", { name: "Try again" }));
		expect(retry).toHaveBeenCalledOnce();
	});
});

it("shows API conflict details so authors can correct their input", () => {
	render(<PlatformError error={{ detail: "Suite name already exists" }} />);
	expect(screen.getByRole("alert")).toHaveTextContent(
		"Suite name already exists",
	);
});
