import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mock = vi.hoisted(() => ({ canPlatformRead: false }));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: (permission: string) =>
			permission === "platform.read" && mock.canPlatformRead,
	}),
}));

vi.mock("./VersionUpdateBanner", () => ({
	VersionUpdateBanner: () => <div>Version status</div>,
}));
vi.mock("./FileActivityIndicator", () => ({
	FileActivityIndicator: () => <div>File activity</div>,
}));

import { HeaderStatusIndicators } from "./HeaderStatusIndicators";

describe("HeaderStatusIndicators", () => {
	it("shows file activity for a named platform.read holder", () => {
		mock.canPlatformRead = true;
		renderWithProviders(<HeaderStatusIndicators />);

		expect(screen.getByText("File activity")).toBeVisible();
	});

	it("hides file activity without Global platform.read", () => {
		mock.canPlatformRead = false;
		renderWithProviders(<HeaderStatusIndicators />);

		expect(screen.queryByText("File activity")).not.toBeInTheDocument();
	});
});
