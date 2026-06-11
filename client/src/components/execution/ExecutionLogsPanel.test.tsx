/**
 * Component tests for ExecutionLogsPanel.
 *
 * The panel is a single framed "inspector" surface (header band + log list)
 * whose empty-state copy varies by status bucket (running / complete /
 * unknown). We cover:
 *   - empty-state copy per status bucket
 *   - logs render in order (including order preservation when logs are
 *     appended via re-render, simulating streaming updates)
 *   - the copy button writes a formatted blob to the clipboard
 *   - the Live badge shows while streaming is running and connected
 */

import { describe, it, expect, vi, beforeEach, MockInstance } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { ExecutionLogsPanel } from "./ExecutionLogsPanel";
import type { LogEntry } from "./ExecutionLogsPanel";

function log(level: string, message: string, ts = "2026-04-20T12:00:00Z"): LogEntry {
	return { level, message, timestamp: ts } as LogEntry;
}

let writeTextSpy: MockInstance;

beforeEach(() => {
	// happy-dom exposes `navigator.clipboard` as a getter on the prototype
	// that returns the same Clipboard instance, so spying on writeText via
	// that instance is the cleanest way to capture the call.
	writeTextSpy = vi
		.spyOn(navigator.clipboard, "writeText")
		.mockResolvedValue(undefined);
});

describe("ExecutionLogsPanel — empty states", () => {
	it("shows 'Waiting for logs...' while running with no logs yet", () => {
		renderWithProviders(
			<ExecutionLogsPanel logs={[]} status="Running" isConnected={true} />,
		);
		expect(screen.getByText(/waiting for logs/i)).toBeInTheDocument();
	});

	it("shows 'No logs captured' when a completed execution has none", () => {
		renderWithProviders(<ExecutionLogsPanel logs={[]} status="Success" />);
		expect(screen.getByText(/no logs captured/i)).toBeInTheDocument();
	});

	it("shows 'No execution in progress' when status is undefined", () => {
		renderWithProviders(<ExecutionLogsPanel logs={[]} />);
		expect(screen.getByText(/no execution in progress/i)).toBeInTheDocument();
	});
});

describe("ExecutionLogsPanel — rendering logs", () => {
	it("renders each log's level and message", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[
					log("INFO", "started"),
					log("WARNING", "slow query"),
					log("ERROR", "boom"),
				]}
			/>,
		);
		expect(screen.getByText("started")).toBeInTheDocument();
		expect(screen.getByText("slow query")).toBeInTheDocument();
		expect(screen.getByText("boom")).toBeInTheDocument();
		// Levels render upper-cased in a dedicated column.
		expect(screen.getByText("INFO")).toBeInTheDocument();
		expect(screen.getByText("WARNING")).toBeInTheDocument();
		expect(screen.getByText("ERROR")).toBeInTheDocument();
	});

	it("preserves log order when new logs are appended (streaming)", () => {
		const initial = [log("INFO", "first"), log("INFO", "second")];
		const { rerender } = renderWithProviders(
			<ExecutionLogsPanel status="Running" logs={initial} />,
		);
		expect(screen.getByText("first")).toBeInTheDocument();
		expect(screen.getByText("second")).toBeInTheDocument();

		// Simulate a new log streaming in.
		rerender(
			<ExecutionLogsPanel
				status="Running"
				logs={[...initial, log("INFO", "third")]}
			/>,
		);

		// All three are present in the DOM in order.
		const messages = ["first", "second", "third"].map((m) =>
			screen.getByText(m),
		);
		// Confirm DOM order by walking the rendered NodeList.
		const positions = messages.map(
			(el) =>
				Array.from(document.body.querySelectorAll("*")).indexOf(el) >>> 0,
		);
		expect(positions).toEqual([...positions].sort((a, b) => a - b));
	});

	it("hides the level-filter note when the viewer is a platform admin", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "hi")]}
				isPlatformAdmin={true}
			/>,
		);
		expect(
			screen.queryByText(/INFO and above/i),
		).not.toBeInTheDocument();
	});

	it("tells non-admin viewers which levels are filtered", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "hi")]}
				isPlatformAdmin={false}
			/>,
		);
		expect(
			screen.getByText(/INFO and above/i),
		).toBeInTheDocument();
	});
});

describe("ExecutionLogsPanel — copy button", () => {
	it("writes formatted text to the clipboard when clicked", async () => {
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "hello"), log("ERROR", "oh no")]}
			/>,
		);

		// The running variant's copy button is an icon-only ghost button with
		// no accessible name, so we target it by the card header region.
		const buttons = screen.getAllByRole("button");
		expect(buttons.length).toBeGreaterThan(0);
		await user.click(buttons[0]);

		expect(writeTextSpy).toHaveBeenCalledTimes(1);
		const payload = writeTextSpy.mock.calls[0][0] as string;
		expect(payload).toContain("INFO");
		expect(payload).toContain("hello");
		expect(payload).toContain("ERROR");
		expect(payload).toContain("oh no");
	});
});

describe("ExecutionLogsPanel — live streaming header", () => {
	it("renders a Live badge while streaming is connected", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				isConnected={true}
				logs={[log("INFO", "streaming")]}
			/>,
		);
		expect(screen.getByText(/live/i)).toBeInTheDocument();
		// The header band labels the region with "Logs".
		const header = screen.getByText("Logs");
		expect(header).toBeInTheDocument();
		// And the streamed message body is present.
		expect(
			within(document.body).getByText("streaming"),
		).toBeInTheDocument();
	});

	it("omits the Live badge when not connected", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				isConnected={false}
				logs={[]}
			/>,
		);
		expect(screen.queryByText(/live/i)).not.toBeInTheDocument();
	});
});

describe("ExecutionLogsPanel — traceback coalescing", () => {
	it("coalesces consecutive TRACEBACK lines into one block", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Failed"
				logs={[
					log("INFO", "starting"),
					log("TRACEBACK", "Traceback (most recent call last):"),
					log("TRACEBACK", '  File "wf.py", line 3, in run'),
					log("TRACEBACK", "RuntimeError: boom"),
				]}
			/>,
		);
		const blocks = screen.getAllByTestId("log-traceback-block");
		expect(blocks).toHaveLength(1);
		// The block contains all three lines joined.
		expect(blocks[0]).toHaveTextContent("Traceback (most recent call last):");
		expect(blocks[0]).toHaveTextContent("RuntimeError: boom");
		// The repeated level label renders exactly once (exact node match —
		// the message text also contains the word "Traceback").
		expect(within(blocks[0]).getAllByText("traceback")).toHaveLength(1);
	});

	it("starts a new block when tracebacks are separated by other levels", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Failed"
				logs={[
					log("TRACEBACK", "first"),
					log("ERROR", "between"),
					log("TRACEBACK", "second"),
				]}
			/>,
		);
		expect(screen.getAllByTestId("log-traceback-block")).toHaveLength(2);
	});
});

describe("ExecutionLogsPanel — header affordances", () => {
	it("shows a copy button and line count in the header band", async () => {
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "one"), log("INFO", "two")]}
			/>,
		);
		expect(screen.getByText(/2 lines/i)).toBeInTheDocument();
		await user.click(screen.getByTitle(/copy logs/i));
		expect(writeTextSpy).toHaveBeenCalledWith(
			expect.stringContaining("one"),
		);
	});
});
