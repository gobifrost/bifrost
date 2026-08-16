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
	});

	it("collapses completed details behind elapsed time", async () => {
		const { user } = renderWithProviders(
			<ChatRunActivity isActive={false} durationMs={74_000}>
				<span>create_text_artifact</span>
			</ChatRunActivity>,
		);

		expect(screen.getByText("Worked for 1m 14s")).toBeInTheDocument();
		expect(screen.queryByText("create_text_artifact")).not.toBeInTheDocument();
		await user.click(screen.getByRole("button"));
		expect(screen.getByText("create_text_artifact")).toBeInTheDocument();
	});
});

describe("formatRunDuration", () => {
	it("formats short and minute-scale runs", () => {
		expect(formatRunDuration(500)).toBe("less than a second");
		expect(formatRunDuration(125_000)).toBe("2m 5s");
	});
});
