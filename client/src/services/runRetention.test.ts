import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import { ApiError } from "@/lib/api-error";
import {
	getRunRetention,
	previewRunRetention,
	startRunRetention,
	updateRunRetention,
	useRunRetentionDays,
} from "./runRetention";

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status });

describe("run retention service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads and updates the retention settings", async () => {
		authFetchMock
			.mockResolvedValueOnce(json({ settings: { days: 30 } }))
			.mockResolvedValueOnce(json({ settings: { days: null } }));

		await expect(getRunRetention()).resolves.toEqual({
			settings: { days: 30 },
		});
		await updateRunRetention({ days: null });

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/run-retention/settings",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/run-retention/settings",
			expect.objectContaining({ method: "PUT", body: '{"days":null}' }),
		);
	});

	it("previews a window, omitting it to keep runs forever", async () => {
		const preview = {
			days: 30,
			cutoff: "2026-09-05T03:00:00Z",
			workflow_runs: 12,
			agent_runs: 3,
			events: 40,
		};
		authFetchMock
			.mockResolvedValueOnce(json(preview))
			.mockResolvedValueOnce(json({ ...preview, days: null }));

		await expect(previewRunRetention(30)).resolves.toEqual(preview);
		await previewRunRetention(null);

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/run-retention/preview?days=30",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/run-retention/preview",
			undefined,
		);
	});

	it("starts a real run or a dry run", async () => {
		const accepted = {
			job_id: "job-1",
			status: "queued",
			reused: false,
			notification_id: null,
		};
		authFetchMock
			.mockResolvedValueOnce(json(accepted, 202))
			.mockResolvedValueOnce(json(accepted, 202));

		await expect(startRunRetention(true)).resolves.toEqual(accepted);
		await startRunRetention(false);

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/run-retention/run",
			expect.objectContaining({
				method: "POST",
				body: '{"dry_run":true}',
			}),
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/run-retention/run",
			expect.objectContaining({
				method: "POST",
				body: '{"dry_run":false}',
			}),
		);
	});

	it("rejects with the status and the server's reason", async () => {
		authFetchMock.mockResolvedValueOnce(
			json({ detail: "days must be at least 30" }, 422),
		);

		const error = await updateRunRetention({ days: 7 }).catch(
			(caught: unknown) => caught,
		);

		expect(error).toBeInstanceOf(ApiError);
		expect((error as ApiError).statusCode).toBe(422);
		expect((error as ApiError).message).toContain("days must be at least 30");
	});

	it("falls back to the status text when the error has no detail", async () => {
		authFetchMock.mockResolvedValueOnce(
			new Response("nope", { status: 502, statusText: "Bad Gateway" }),
		);

		await expect(getRunRetention()).rejects.toThrow(
			"Run retention request failed: Bad Gateway",
		);
	});

	describe("useRunRetentionDays", () => {
		function renderDays() {
			const client = new QueryClient({
				defaultOptions: { queries: { retry: false } },
			});
			const wrapper = ({ children }: { children: ReactNode }) =>
				createElement(QueryClientProvider, { client }, children);
			return renderHook(() => useRunRetentionDays(), { wrapper });
		}

		it("is undefined while loading, then the retained days", async () => {
			authFetchMock.mockResolvedValueOnce(json({ days: 45 }));

			const { result } = renderDays();

			expect(result.current).toBeUndefined();
			await waitFor(() => expect(result.current).toBe(45));
			expect(authFetchMock).toHaveBeenCalledWith(
				"/api/run-retention",
				undefined,
			);
		});

		it("reports null when runs are kept forever", async () => {
			authFetchMock.mockResolvedValueOnce(json({ days: null }));

			const { result } = renderDays();

			await waitFor(() => expect(result.current).toBeNull());
		});
	});
});
