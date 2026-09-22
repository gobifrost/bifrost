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
import { renderWithProviders, screen, within, fireEvent } from "@/test-utils";
import { ExecutionLogsPanel } from "./ExecutionLogsPanel";
import type { LogEntry } from "./ExecutionLogsPanel";
import * as clipboard from "@/lib/clipboard";
import { toast } from "sonner";

function log(
	level: string,
	message: string,
	ts = "2026-04-20T12:00:00Z",
): LogEntry {
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
			<ExecutionLogsPanel
				logs={[]}
				status="Running"
				isConnected={true}
			/>,
		);
		expect(screen.getByText(/waiting for logs/i)).toBeInTheDocument();
	});

	it("shows 'No logs captured' when a completed execution has none", () => {
		renderWithProviders(<ExecutionLogsPanel logs={[]} status="Success" />);
		expect(screen.getByText(/no logs captured/i)).toBeInTheDocument();
	});

	it("shows 'No execution in progress' when status is undefined", () => {
		renderWithProviders(<ExecutionLogsPanel logs={[]} />);
		expect(
			screen.getByText(/no execution in progress/i),
		).toBeInTheDocument();
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
				Array.from(document.body.querySelectorAll("*")).indexOf(el) >>>
				0,
		);
		expect(positions).toEqual([...positions].sort((a, b) => a - b));
	});

	it("renders newest-first with sortOrder=desc", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[
					log("INFO", "older", "2026-04-20T12:00:00Z"),
					log("INFO", "newer", "2026-04-20T12:05:00Z"),
				]}
				sortOrder="desc"
			/>,
		);
		const newer = screen.getByText("newer");
		const older = screen.getByText("older");
		expect(
			newer.compareDocumentPosition(older) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
	});

	it("pins arrivals to the top while following newest-first", () => {
		const { rerender } = renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "first")]}
				sortOrder="desc"
			/>,
		);
		rerender(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "first"), log("INFO", "second")]}
				sortOrder="desc"
			/>,
		);
		// Newest renders first; no "Following paused" footer appears
		// because the follow edge (top) was never left.
		const newer = screen.getByText("second");
		const older = screen.getByText("first");
		expect(
			newer.compareDocumentPosition(older) &
				Node.DOCUMENT_POSITION_FOLLOWING,
		).toBeTruthy();
		expect(screen.queryByText("Following paused")).not.toBeInTheDocument();
	});

	it("renders the pagination bar with follow control and pager", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "only")]}
				sortOrder="desc"
				isConnected={true}
				pagination={<span>pager-node</span>}
			/>,
		);
		expect(screen.getByText("pager-node")).toBeInTheDocument();
		expect(
			screen.getByText("Following live updates"),
		).toBeInTheDocument();
	});

	it("pauses into Back to Top inside the bar, without a floating duplicate", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "only")]}
				sortOrder="desc"
				pagination={<span>pager-node</span>}
			/>,
		);
		const region = screen.getByRole("region", {
			name: "Execution log messages",
		});
		Object.defineProperty(region, "scrollHeight", {
			value: 1000,
			configurable: true,
		});
		Object.defineProperty(region, "clientHeight", {
			value: 100,
			configurable: true,
		});
		region.scrollTop = 500;
		fireEvent.scroll(region);
		expect(
			screen.getAllByRole("button", { name: "Back to Top" }),
		).toHaveLength(1);
	});

	it("keeps the floating pause control when there is no pagination", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "only")]}
			/>,
		);
		const region = screen.getByRole("region", {
			name: "Execution log messages",
		});
		Object.defineProperty(region, "scrollHeight", {
			value: 1000,
			configurable: true,
		});
		Object.defineProperty(region, "clientHeight", {
			value: 100,
			configurable: true,
		});
		region.scrollTop = 500;
		fireEvent.scroll(region);
		expect(
			screen.getByRole("button", { name: "Jump to latest" }),
		).toBeInTheDocument();
		expect(screen.getByText("Following paused")).toBeInTheDocument();
	});

	it("hides the level-filter note when the viewer is a platform admin", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "hi")]}
				isPlatformAdmin={true}
			/>,
		);
		expect(screen.queryByText(/INFO and above/i)).not.toBeInTheDocument();
	});
	it("tells non-admin viewers which levels are filtered", () => {
		renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "hi")]}
				isPlatformAdmin={false}
			/>,
		);
		expect(screen.getByText(/INFO and above/i)).toBeInTheDocument();
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

	it("toasts an error when copying fails", async () => {
		const copySpy = vi
			.spyOn(clipboard, "copyToClipboard")
			.mockResolvedValue(false);
		const toastSpy = vi
			.spyOn(toast, "error")
			.mockImplementation(() => "copy-error");
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Running"
				logs={[log("INFO", "hello")]}
			/>,
		);

		await user.click(screen.getByTitle(/copy logs/i));

		expect(copySpy).toHaveBeenCalledTimes(1);
		expect(toastSpy).toHaveBeenCalledWith("Failed to copy logs");
		copySpy.mockRestore();
		toastSpy.mockRestore();
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
		expect(
			screen.queryByText("Live", { exact: true }),
		).not.toBeInTheDocument();
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
		expect(blocks[0]).toHaveTextContent(
			"Traceback (most recent call last):",
		);
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

describe("ExecutionLogsPanel — filtering and export", () => {
	it("filters visible log lines by search text", async () => {
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[
					log("INFO", "invoice started"),
					log("ERROR", "payment token rejected"),
				]}
			/>,
		);

		await user.type(
			screen.getByRole("textbox", { name: "Search logs" }),
			"token",
		);

		expect(screen.getByText("payment token rejected")).toBeVisible();
		expect(screen.queryByText("invoice started")).not.toBeInTheDocument();
		expect(screen.getByText(/1 visible/i)).toBeVisible();
	});

	it("filters visible log lines by level", async () => {
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "started"), log("ERROR", "failed")]}
			/>,
		);

		await user.click(screen.getByRole("combobox"));
		await user.click(screen.getByRole("option", { name: "Error" }));

		expect(screen.getByText("failed")).toBeVisible();
		expect(screen.queryByText("started")).not.toBeInTheDocument();
	});

	it("downloads the full unfiltered log stream", async () => {
		const createObjectURL = vi
			.spyOn(URL, "createObjectURL")
			.mockReturnValue("blob:execution-logs");
		const revokeObjectURL = vi
			.spyOn(URL, "revokeObjectURL")
			.mockImplementation(() => {});
		const click = vi
			.spyOn(HTMLAnchorElement.prototype, "click")
			.mockImplementation(() => {});
		const { user } = renderWithProviders(
			<ExecutionLogsPanel
				status="Success"
				logs={[log("INFO", "one"), log("ERROR", "two")]}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Download all logs" }),
		);

		expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
		expect(click).toHaveBeenCalledOnce();
		expect(revokeObjectURL).toHaveBeenCalledWith("blob:execution-logs");
		click.mockRestore();
		createObjectURL.mockRestore();
		revokeObjectURL.mockRestore();
	});
});

it("keeps the reader's position during streaming and resumes following explicitly", async () => {
	const initial = [log("INFO", "earlier message")];
	const { user, rerender } = renderWithProviders(
		<ExecutionLogsPanel logs={initial} status="Running" />,
	);
	const region = screen.getByRole("region", {
		name: "Execution log messages",
	});
	Object.defineProperties(region, {
		scrollHeight: { configurable: true, value: 1000 },
		clientHeight: { configurable: true, value: 200 },
	});
	region.scrollTop = 100;
	fireEvent.scroll(region);
	expect(screen.getByText("Following paused")).toBeInTheDocument();
	rerender(
		<ExecutionLogsPanel
			logs={[...initial, log("INFO", "new message")]}
			status="Running"
		/>,
	);
	expect(region.scrollTop).toBe(100);
	await user.click(screen.getByRole("button", { name: "Jump to latest" }));
	expect(region.scrollTop).toBe(1000);
	expect(screen.queryByText("Following paused")).not.toBeInTheDocument();
});

it("retains logs during a connection interruption and clears the notice on recovery", () => {
	const logs = [log("INFO", "Last received message")];
	const { rerender } = renderWithProviders(
		<ExecutionLogsPanel logs={logs} status="Running" isConnected={false} />,
	);
	expect(screen.getByRole("status")).toHaveTextContent(
		"New log messages may be delayed",
	);
	expect(screen.getByText("Last received message")).toBeVisible();
	rerender(<ExecutionLogsPanel logs={logs} status="Running" isConnected />);
	expect(
		screen.queryByText(/connection unavailable/i),
	).not.toBeInTheDocument();
	expect(screen.getByText("Live", { exact: true })).toBeVisible();
	rerender(
		<ExecutionLogsPanel logs={logs} status="Success" isConnected={false} />,
	);
	expect(
		screen.queryByText(/connection unavailable/i),
	).not.toBeInTheDocument();
	expect(screen.getByText("Last received message")).toBeVisible();
});

describe("ExecutionLogsPanel — date filter", () => {
	const logs = [
		log("info", "old line", "2026-04-17T09:30:12Z"),
		log("info", "new line", "2026-04-20T07:12:44Z"),
	];

	it("hides the picker unless showDateFilter is set", () => {
		renderWithProviders(<ExecutionLogsPanel logs={logs} />);
		expect(
			screen.queryByRole("button", { name: "Pick a date range" }),
		).not.toBeInTheDocument();
	});

	it("narrows lines to the selected day window", () => {
		const day = new Date("2026-04-20T07:12:44Z");
		const { rerender } = renderWithProviders(
			<ExecutionLogsPanel logs={logs} showDateFilter />,
		);
		expect(screen.getByText("old line")).toBeInTheDocument();
		rerender(
			<ExecutionLogsPanel
				logs={logs}
				showDateFilter
				dateRange={{ from: day, to: day }}
			/>,
		);
		expect(screen.queryByText("old line")).not.toBeInTheDocument();
		expect(screen.getByText("new line")).toBeInTheDocument();
	});
});
