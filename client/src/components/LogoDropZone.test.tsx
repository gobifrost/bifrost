import userEvent from "@testing-library/user-event";
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
		expect(img.getAttribute("src")).toBe(baseProps.previewUrl);
		expect(img).toHaveClass("opacity-0");
		fireEvent.load(img);
		expect(img).toHaveClass("opacity-100");
	});

	it("hides the placeholder behind loaded transparent logos and restores it on error", () => {
		render(
			<LogoDropZone
				{...baseProps}
				fallback={<span>Default Logo</span>}
			/>,
		);
		const image = screen
			.getByRole("group", { name: "Upload image (click or drag)" })
			.querySelector("img");
		expect(image).not.toBeNull();
		fireEvent.load(image!);
		expect(screen.queryByText("Default Logo")).not.toBeInTheDocument();
		fireEvent.error(image!);
		expect(screen.getByText("Default Logo")).toBeVisible();
	});

	it("shows the fallback when the preview img errors", () => {
		render(<LogoDropZone {...baseProps} />);
		const img = screen.getByTestId("logo-drop-zone-img");
		fireEvent.error(img);
		expect(screen.getByTestId("fallback")).toBeInTheDocument();
	});

	it("POSTs the dropped file via authFetch", async () => {
		mockAuthFetch.mockResolvedValueOnce(
			new Response(null, { status: 200 }),
		);
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
			fireEvent.click(
				screen.getByRole("button", {
					name: "Upload image (click or drag)",
				}),
			);
			expect(click).toHaveBeenCalled();
		} finally {
			HTMLInputElement.prototype.click = orig;
		}
	});

	it("opens the file picker from the keyboard", async () => {
		const user = userEvent.setup();
		const orig = HTMLInputElement.prototype.click;
		const click = vi.fn();
		HTMLInputElement.prototype.click = click;
		try {
			render(<LogoDropZone {...baseProps} />);
			screen
				.getByRole("button", { name: "Upload image (click or drag)" })
				.focus();
			await user.keyboard("{Enter}");
			expect(click).toHaveBeenCalled();
		} finally {
			HTMLInputElement.prototype.click = orig;
		}
	});

	it("DELETEs via authFetch when Remove is clicked", async () => {
		mockAuthFetch.mockResolvedValueOnce(
			new Response(null, { status: 204 }),
		);
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
	it("does not open the picker when the separate remove button receives a key", () => {
		const click = vi
			.spyOn(HTMLInputElement.prototype, "click")
			.mockImplementation(() => {});
		try {
			render(<LogoDropZone {...baseProps} />);
			fireEvent.load(screen.getByTestId("logo-drop-zone-img"));
			fireEvent.keyDown(
				screen.getByRole("button", { name: "Remove image" }),
				{ key: "Enter" },
			);
			expect(click).not.toHaveBeenCalled();
		} finally {
			click.mockRestore();
		}
	});

	it("ignores a second dropped file while an upload is pending", async () => {
		let resolveUpload!: (response: Response) => void;
		mockAuthFetch.mockImplementationOnce(
			() =>
				new Promise<Response>((resolve) => {
					resolveUpload = resolve;
				}),
		);
		render(<LogoDropZone {...baseProps} />);
		const zone = screen.getByTestId("logo-drop-zone");
		const file = new File(["x"], "logo.png", { type: "image/png" });
		fireEvent.drop(zone, { dataTransfer: { files: [file] } });
		fireEvent.drop(zone, { dataTransfer: { files: [file] } });
		expect(mockAuthFetch).toHaveBeenCalledTimes(1);
		resolveUpload(new Response(null, { status: 200 }));
		await waitFor(() => expect(zone).toHaveAttribute("aria-busy", "false"));
	});
});
