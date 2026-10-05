import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "./provider";
import { useOrganizations } from "./use-organizations";

const ORGANIZATIONS = [
	{
		id: "org-1",
		name: "Acme",
		is_active: true,
		is_provider: false,
		created_at: null,
		created_by: "admin@example.test",
	},
];

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason?: unknown) => void;
	const promise = new Promise<T>((nextResolve, nextReject) => {
		resolve = nextResolve;
		reject = nextReject;
	});
	return { promise, resolve, reject };
}

function Organizations({
	enabled = true,
	includeInactive = false,
}: {
	enabled?: boolean;
	includeInactive?: boolean;
}) {
	const { data, loading, error, refetch } = useOrganizations({
		enabled,
		includeInactive,
	});
	return (
		<>
			<output aria-label="organizations">
				{loading
					? "loading"
					: error
						? `error:${error.message}`
						: (data?.map((organization) => organization.name).join(",") ?? "empty")}
			</output>
			<button onClick={() => void refetch()}>refresh</button>
		</>
	);
}

describe("useOrganizations", () => {
	it("loads organizations and includes the inactive option in the API request", async () => {
		const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
			expect(String(input)).toBe(
				"https://api.example/api/organizations?include_inactive=true",
			);
			return new Response(JSON.stringify(ORGANIZATIONS), { status: 200 });
		});

		render(
			<BifrostProvider baseUrl="https://api.example" token="token" fetchImpl={fetchImpl}>
				<Organizations includeInactive />
			</BifrostProvider>,
		);

		await screen.findByText("Acme");
		expect(fetchImpl).toHaveBeenCalledTimes(1);
	});

	it("keeps authorization failures visible with their HTTP status", async () => {
		render(
			<BifrostProvider
				baseUrl="/"
				token="token"
				fetchImpl={vi.fn(async () => new Response("forbidden", { status: 403 }))}
			>
				<Organizations />
			</BifrostProvider>,
		);

		await waitFor(() =>
			expect(screen.getByRole("status", { name: "organizations" })).toHaveTextContent(
				"error:403",
			),
		);
	});

	it("does not fetch while disabled, clears prior data, and fetches after enabling", async () => {
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify(ORGANIZATIONS), { status: 200 }),
		);
		const view = render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Organizations />
			</BifrostProvider>,
		);
		await screen.findByText("Acme");

		view.rerender(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Organizations enabled={false} />
			</BifrostProvider>,
		);
		await waitFor(() =>
			expect(screen.getByRole("status", { name: "organizations" })).toHaveTextContent("empty"),
		);
		expect(fetchImpl).toHaveBeenCalledTimes(1);

		view.rerender(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Organizations enabled />
			</BifrostProvider>,
		);
		await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
		await screen.findByText("Acme");
	});

	it("refetches on demand and exposes malformed JSON errors", async () => {
		let calls = 0;
		const fetchImpl = vi.fn(async () => {
			calls += 1;
			return calls === 1
				? new Response(JSON.stringify(ORGANIZATIONS), { status: 200 })
				: new Response("not json", { status: 200 });
		});
		render(
			<BifrostProvider baseUrl="/" token="token" fetchImpl={fetchImpl}>
				<Organizations />
			</BifrostProvider>,
		);
		await screen.findByText("Acme");
		await act(async () => screen.getByRole("button", { name: "refresh" }).click());
		await waitFor(() =>
			expect(screen.getByRole("status", { name: "organizations" })).toHaveTextContent("error:"),
		);
		expect(fetchImpl).toHaveBeenCalledTimes(2);
	});

	it("discards a stale response after the provider scope changes", async () => {
		const oldResponse = deferred<Response>();
		const fetchImpl = vi
			.fn()
			.mockImplementationOnce(() => oldResponse.promise)
			.mockResolvedValueOnce(new Response(JSON.stringify([{ ...ORGANIZATIONS[0], name: "New scope" }])));
		const view = render(
			<BifrostProvider baseUrl="https://api.example" token="one" orgScope="org-old" fetchImpl={fetchImpl}>
				<Organizations />
			</BifrostProvider>,
		);
		await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

		view.rerender(
			<BifrostProvider baseUrl="https://api.example" token="two" orgScope="org-new" fetchImpl={fetchImpl}>
				<Organizations />
			</BifrostProvider>,
		);
		await screen.findByText("New scope");
		await act(async () => oldResponse.resolve(new Response(JSON.stringify(ORGANIZATIONS))));
		expect(screen.getByRole("status", { name: "organizations" })).toHaveTextContent("New scope");
	});

	it("surfaces network failures", async () => {
		render(
			<BifrostProvider
				baseUrl="/"
				token="token"
				fetchImpl={vi.fn(async () => Promise.reject(new Error("offline")))}
			>
				<Organizations />
			</BifrostProvider>,
		);
		await screen.findByText("error:offline");
	});
});
