import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import { QueueBanner } from "./QueueBanner";

describe("QueueBanner", () => {
	it("renders nothing when count is 0", () => {
		const { container } = renderWithProviders(<QueueBanner count={0} />);
		expect(container.firstChild).toBeNull();
	});

	it("renders count text and pluralizes correctly (1)", () => {
		renderWithProviders(<QueueBanner count={1} />);
		expect(
			screen.getByText(/1 flagged run to review/i),
		).toBeInTheDocument();
	});

	it("renders count text and pluralizes correctly (5)", () => {
		renderWithProviders(<QueueBanner count={5} />);
		expect(
			screen.getByText(/5 flagged runs to review/i),
		).toBeInTheDocument();
	});

	it("renders an action button when onAction is provided", async () => {
		const onAction = vi.fn();
		const { user } = renderWithProviders(
			<QueueBanner count={2} onAction={onAction} />,
		);
		await user.click(screen.getByRole("button", { name: /review runs/i }));
		expect(onAction).toHaveBeenCalled();
	});

	it("renders an action link when actionHref is provided", () => {
		renderWithProviders(
			<QueueBanner count={2} actionHref="/agents/abc/quality" />,
		);
		const link = screen.getByRole("link", { name: /review runs/i });
		expect(link).toHaveAttribute("href", "/agents/abc/quality");
	});

	it("renders a dismiss button when onDismiss is provided", async () => {
		const onDismiss = vi.fn();
		const { user } = renderWithProviders(
			<QueueBanner count={2} onDismiss={onDismiss} />,
		);
		await user.click(screen.getByRole("button", { name: /dismiss/i }));
		expect(onDismiss).toHaveBeenCalled();
	});

	it("uses a custom action label when provided", () => {
		renderWithProviders(
			<QueueBanner
				count={2}
				actionLabel="Review now"
				onAction={() => {}}
			/>,
		);
		expect(
			screen.getByRole("button", { name: /review now/i }),
		).toBeInTheDocument();
	});

	it("uses a custom description when provided", () => {
		renderWithProviders(
			<QueueBanner count={2} description="Custom subtitle text." />,
		);
		expect(screen.getByText(/custom subtitle text/i)).toBeInTheDocument();
	});
});
