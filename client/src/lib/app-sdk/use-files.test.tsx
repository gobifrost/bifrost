import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const subscribeMock = vi.fn();
let lastOnEvent: ((evt: Record<string, unknown>) => void) | null = null;
let lastUnsubscribe: ReturnType<typeof vi.fn> | null = null;

vi.mock("./ws-client", () => ({
	subscribeToFiles: (
		location: string,
		prefix: string,
		scope: string | null | undefined,
		cb: (evt: Record<string, unknown>) => void,
	) => {
		lastOnEvent = cb;
		lastUnsubscribe = vi.fn(() => {
			lastOnEvent = null;
		});
		subscribeMock(location, prefix, scope, cb);
		return lastUnsubscribe;
	},
}));

import { useFiles } from "./use-files";

function okJson(body: unknown) {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { "content-type": "application/json" },
	});
}

describe("useFiles", () => {
	beforeEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
		subscribeMock.mockClear();
		lastOnEvent = null;
		lastUnsubscribe = null;
	});

	// Unstub the global `fetch` after the last test too, so the stub does not
	// leak past this file's boundary and pollute later suites' data fetching.
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it("loads the initial REST list and subscribes to files:{location}:{prefix}", async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			okJson({
				files: ["inbox/a.txt"],
				files_metadata: [],
			}),
		);
		vi.stubGlobal("fetch", fetchMock);

		const { result } = renderHook(() =>
			useFiles("inbox", { location: "shared" }),
		);

		await waitFor(() => expect(result.current.loading).toBe(false));
		expect(result.current.files).toEqual(["inbox/a.txt"]);
		expect(result.current.denied).toBe(false);
		expect(result.current.empty).toBe(false);
		expect(subscribeMock).toHaveBeenCalledWith(
			"shared",
			"inbox",
			null,
			expect.any(Function),
		);
		expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
			directory: "inbox",
			location: "shared",
		});
	});

	it("passes scope into the file subscription", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(okJson({ files: [], files_metadata: [] })),
		);

		renderHook(() =>
			useFiles("gallery", {
				location: "shared",
				scope: "00000000-0000-0000-0000-000000000001",
			}),
		);

		await waitFor(() =>
			expect(subscribeMock).toHaveBeenCalledWith(
				"shared",
				"gallery",
				"00000000-0000-0000-0000-000000000001",
				expect.any(Function),
			),
		);
	});

	it("exposes empty folders separately from denied folders", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(okJson({ files: [], files_metadata: [] })),
		);

		const { result } = renderHook(() => useFiles(""));

		await waitFor(() => expect(result.current.loading).toBe(false));
		expect(result.current.empty).toBe(true);
		expect(result.current.denied).toBe(false);
		expect(result.current.error).toBeNull();
	});

	it("exposes access denial without treating it as empty", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(new Response("policy denied", { status: 403 })),
		);

		const { result } = renderHook(() => useFiles("private"));

		await waitFor(() => expect(result.current.loading).toBe(false));
		expect(result.current.files).toEqual([]);
		expect(result.current.denied).toBe(true);
		expect(result.current.empty).toBe(false);
		expect(result.current.error?.name).toBe("FileAccessDeniedError");
	});

	it("refetches the list when a file change event arrives", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(okJson({ files: ["a.txt"], files_metadata: [] }))
			.mockResolvedValueOnce(okJson({ files: ["a.txt", "b.txt"], files_metadata: [] }));
		vi.stubGlobal("fetch", fetchMock);

		const { result } = renderHook(() => useFiles(""));
		await waitFor(() => expect(result.current.files).toEqual(["a.txt"]));

		act(() => {
			lastOnEvent?.({ type: "file_change", path: "b.txt" });
		});

		await waitFor(() =>
			expect(result.current.files).toEqual(["a.txt", "b.txt"]),
		);
		expect(fetchMock).toHaveBeenCalledTimes(2);
	});

	it("cleans up the subscription on unmount", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(okJson({ files: [], files_metadata: [] })),
		);

		const { unmount } = renderHook(() => useFiles("logs"));
		await waitFor(() => expect(lastUnsubscribe).not.toBeNull());
		unmount();

		expect(lastUnsubscribe).toHaveBeenCalledTimes(1);
	});

	it("closes the subscription when the server revokes it", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(okJson({ files: [], files_metadata: [] })),
		);

		const { result } = renderHook(() => useFiles("logs"));
		await waitFor(() => expect(result.current.loading).toBe(false));

		act(() => {
			lastOnEvent?.({
				type: "subscription_revoked",
				channel: "files:workspace:logs",
			});
		});

		expect(lastUnsubscribe).toHaveBeenCalledTimes(1);
		expect(result.current.denied).toBe(true);
	});
});
