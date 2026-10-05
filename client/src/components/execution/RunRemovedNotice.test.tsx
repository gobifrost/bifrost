import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

const mockUseRunRetentionDays = vi.fn<() => number | null | undefined>();

vi.mock("@/services/runRetention", () => ({
	useRunRetentionDays: () => mockUseRunRetentionDays(),
}));

import { RunRemovedNotice } from "./RunRemovedNotice";

describe("RunRemovedNotice", () => {
	beforeEach(() => mockUseRunRetentionDays.mockReset());

	it("names the retention window on the run page", () => {
		mockUseRunRetentionDays.mockReturnValue(30);
		renderWithProviders(<RunRemovedNotice variant="page" />);
		expect(screen.getByRole("status")).toHaveTextContent(
			"This run isn't available. Finished runs are removed after 30 days (retention). It may also be outside your access.",
		);
	});

	it("drops the retention sentence from the page when runs are kept forever", () => {
		mockUseRunRetentionDays.mockReturnValue(null);
		renderWithProviders(<RunRemovedNotice variant="page" />);
		expect(screen.getByRole("status")).toHaveTextContent(
			/^This run isn't available\. It may also be outside your access\.$/,
		);
	});

	it("names the retention window inline", () => {
		mockUseRunRetentionDays.mockReturnValue(30);
		renderWithProviders(<RunRemovedNotice variant="inline" />);
		expect(
			screen.getByText("Removed after 30 days (retention)"),
		).toBeInTheDocument();
	});

	it("says details are gone inline when the window is unknown", () => {
		mockUseRunRetentionDays.mockReturnValue(null);
		renderWithProviders(<RunRemovedNotice variant="inline" />);
		expect(
			screen.getByText("Run details are no longer available"),
		).toBeInTheDocument();
	});
});
