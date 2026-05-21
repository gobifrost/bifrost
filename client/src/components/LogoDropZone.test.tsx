import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LogoDropZone } from "./LogoDropZone";

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: vi.fn(),
}));

import { authFetch } from "@/lib/api-client";

const mockAuthFetch = authFetch as unknown as ReturnType<typeof vi.fn>;

const baseProps = {
	uploadUrl: "/api/applications/abc/logo",
	deleteUrl: "/api/applications/abc/logo",
	previewUrl: "/api/applications/abc/logo",
	fallback: <span data-testid="fallback">F</span>,
	onChange: () => {},
};

describe("LogoDropZone", () => {
	beforeEach(() => {
		mockAuthFetch.mockReset();
	});

	afterEach(() => {
		vi.clearAllMocks();
	});

	it("renders the preview img pointing at previewUrl", () => {
		render(<LogoDropZone {...baseProps} />);
		const img = screen.getByTestId("logo-drop-zone-img");
		expect(img.getAttribute("src")).toContain(baseProps.previewUrl);
	});

	it("shows the fallback when the preview img errors", () => {
		render(<LogoDropZone {...baseProps} />);
		const img = screen.getByTestId("logo-drop-zone-img");
		fireEvent.error(img);
		expect(screen.getByTestId("fallback")).toBeInTheDocument();
	});

	it("POSTs the dropped file via authFetch", async () => {
		mockAuthFetch.mockResolvedValueOnce(new Response(null, { status: 200 }));
		const onChange = vi.fn();
		render(<LogoDropZone {...baseProps} onChange={onChange} />);
		const zone = screen.getByTestId("logo-drop-zone");
		const file = new File(["x"], "logo.png", { type: "image/png" });
		fireEvent.drop(zone, { dataTransfer: { files: [file] } });
		await waitFor(() => expect(onChange).toHaveBeenCalled());
		expect(mockAuthFetch).toHaveBeenCalledWith(
			baseProps.uploadUrl,
			expect.objectContaining({ method: "POST" }),
		);
	});

	it("opens the file picker when the zone is clicked", () => {
		const orig = HTMLInputElement.prototype.click;
		const click = vi.fn();
		HTMLInputElement.prototype.click = click;
		try {
			render(<LogoDropZone {...baseProps} />);
			fireEvent.click(screen.getByTestId("logo-drop-zone"));
			expect(click).toHaveBeenCalled();
		} finally {
			HTMLInputElement.prototype.click = orig;
		}
	});

	it("DELETEs via authFetch when Remove is clicked", async () => {
		mockAuthFetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
		const onChange = vi.fn();
		render(<LogoDropZone {...baseProps} onChange={onChange} />);
		// Remove button only shows once an image has actually loaded
		fireEvent.load(screen.getByTestId("logo-drop-zone-img"));
		fireEvent.click(screen.getByRole("button", { name: /remove/i }));
		await waitFor(() => expect(onChange).toHaveBeenCalled());
		expect(mockAuthFetch).toHaveBeenCalledWith(
			baseProps.deleteUrl,
			expect.objectContaining({ method: "DELETE" }),
		);
	});

	it("rejects files exceeding maxBytes without calling authFetch", async () => {
		render(<LogoDropZone {...baseProps} maxBytes={10} />);
		const zone = screen.getByTestId("logo-drop-zone");
		const file = new File(["A".repeat(100)], "huge.png", {
			type: "image/png",
		});
		fireEvent.drop(zone, { dataTransfer: { files: [file] } });
		await waitFor(() => {
			// give the (rejected) handler a tick to finish
			expect(mockAuthFetch).not.toHaveBeenCalled();
		});
	});
});
