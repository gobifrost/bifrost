import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "./provider";
import { useApiQuery } from "./use-api-query";

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((nextResolve) => {
		resolve = nextResolve;
	});
	return { promise, resolve };
}

function Query({ label }: { label: string }) {
	const { data, loading } = useApiQuery<{ value: string }>("/api/resource");
	return <output aria-label={label}>{loading ? "loading" : data?.value ?? "empty"}</output>;
}

describe("useApiQuery", () => {
	it("shares an in-flight request between consumers and resolves both", async () => {
		const response = deferred<Response>();
		const fetchImpl = vi.fn(() => response.promise) as unknown as typeof fetch;
		render(
			<BifrostProvider baseUrl="https://api.example" token="token" fetchImpl={fetchImpl}>
				<Query label="first" />
				<Query label="second" />
			</BifrostProvider>,
		);
		await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

		await act(async () => response.resolve(new Response(JSON.stringify({ value: "ready" }))));
		expect(screen.getByRole("status", { name: "first" })).toHaveTextContent("ready");
		expect(screen.getByRole("status", { name: "second" })).toHaveTextContent("ready");
	});
});
