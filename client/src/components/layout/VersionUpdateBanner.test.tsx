/**
 * Tests for VersionUpdateBanner.
 *
 * The hook's behavior is covered in useVersionCheck.test.ts; here we just
 * verify the banner renders conditionally and wires Refresh → location.reload.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const useVersionCheckMock = vi.fn<() => boolean>(() => false);

vi.mock("@/hooks/useVersionCheck", () => ({
	useVersionCheck: () => useVersionCheckMock(),
}));

import { VersionUpdateBanner } from "./VersionUpdateBanner";

describe("VersionUpdateBanner", () => {
	it("renders nothing when no update is available", () => {
		useVersionCheckMock.mockReturnValueOnce(false);
		const { container } = render(<VersionUpdateBanner />);
		expect(container.firstChild).toBeNull();
	});

	it("renders a refresh button that reloads the page when an update is available", () => {
		useVersionCheckMock.mockReturnValueOnce(true);

		const reload = vi.fn();
		const originalLocation = window.location;
		Object.defineProperty(window, "location", {
			configurable: true,
			value: { ...originalLocation, reload },
		});

		try {
			render(<VersionUpdateBanner />);
			expect(
				screen.getByText(/A new version of Bifrost is available/i),
			).toBeInTheDocument();

			fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
			expect(reload).toHaveBeenCalledTimes(1);
		} finally {
			Object.defineProperty(window, "location", {
				configurable: true,
				value: originalLocation,
			});
		}
	});
});
