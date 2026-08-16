import { describe, expect, it } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import {
	ChatRunActivity,
	formatRunDuration,
	getActiveRunLabel,
} from "./ChatRunActivity";

describe("ChatRunActivity", () => {
	it("shows the immediate shimmering thinking state without a spinner", () => {
		renderWithProviders(<ChatRunActivity isActive />);

		const status = screen.getByText("Thinking…");
		expect(status).toHaveClass("chat-activity-shimmer");
		expect(status.closest("button")).toHaveClass("min-h-11", "sm:min-h-7");
		expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
	});

	it("uses semantic artifact generation copy", () => {
		expect(
			getActiveRunLabel("create_text_artifact", {
				filename: "Welcome Page.html",
				format: "html",
			}),
		).toBe("Generating HTML…");
		expect(
			getActiveRunLabel("create_text_artifact", {
				filename: "Report.md",
				format: "markdown",
			}),
		).toBe("Generating Markdown…");
		expect(getActiveRunLabel("create_image_artifact", {})).toBe(
			"Generating image…",
		);
		expect(getActiveRunLabel("create_video_artifact", {})).toBe(
			"Starting video generation…",
		);
	});

	it("collapses completed details behind elapsed time", async () => {
		const { user } = renderWithProviders(
			<ChatRunActivity isActive={false} durationMs={74_000}>
				<span>create_text_artifact</span>
			</ChatRunActivity>,
		);

		expect(screen.getByText("Worked for 1m 14s")).toBeInTheDocument();
		const collapsedDetail = screen
			.getByText("create_text_artifact")
			.closest('[aria-hidden="true"]');
		expect(collapsedDetail).toHaveClass("grid-rows-[0fr]", "opacity-0");
		await user.click(screen.getByRole("button"));
		const detail = screen
			.getByText("create_text_artifact")
			.closest('[aria-hidden="false"]');
		expect(detail).toHaveClass("grid-rows-[1fr]", "opacity-100");
		const detailContent = screen.getByText("create_text_artifact").parentElement;
		expect(detailContent).toHaveClass("w-full");
		expect(detailContent).not.toHaveClass("border-l", "pl-3");
	});

	it("keeps running activity collapsed until the user expands it", async () => {
		const { user } = renderWithProviders(
			<ChatRunActivity isActive>
				<span>Ran commands</span>
			</ChatRunActivity>,
		);

		const collapsedDetail = screen
			.getByText("Ran commands")
			.closest('[aria-hidden="true"]');
		expect(collapsedDetail).toHaveClass(
			"grid-rows-[0fr]",
			"opacity-0",
			"duration-200",
		);
		expect(screen.getByRole("button")).toHaveAttribute(
			"aria-expanded",
			"false",
		);

		await user.click(screen.getByRole("button"));
		const expandedDetail = screen
			.getByText("Ran commands")
			.closest('[aria-hidden="false"]');
		expect(expandedDetail).toHaveClass("grid-rows-[1fr]", "duration-300");
		expect(screen.getByRole("button")).toHaveAttribute(
			"aria-expanded",
			"true",
		);
	});
});

describe("formatRunDuration", () => {
	it("formats short and minute-scale runs", () => {
		expect(formatRunDuration(500)).toBe("less than a second");
		expect(formatRunDuration(125_000)).toBe("2m 5s");
	});
});
