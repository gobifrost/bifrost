import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "./provider";
import { RequireRole, useUser } from "./use-user";

const ME = {
	id: "user-1",
	email: "pat@example.test",
	name: "Pat",
	is_active: true,
	is_superuser: false,
	is_verified: true,
	organization_id: "org-1",
	roles: ["authenticated", "Approvers"],
};

function meFetch(body: unknown = ME, status = 200) {
	return vi.fn(async (input: RequestInfo | URL) => {
		expect(String(input)).toBe("https://dev.example/api/auth/me");
		return new Response(JSON.stringify(body), { status });
	}) as unknown as typeof fetch & ReturnType<typeof vi.fn>;
}

function Who() {
	const user = useUser();
	if (user.isLoading) return <span data-testid="who">loading</span>;
	return (
		<span data-testid="who">
			{user.name}|{user.email}|{user.organizationId}|
			{String(user.isPlatformAdmin)}|{String(user.hasRole("Approvers"))}|
			{String(user.hasRole("Admins"))}
		</span>
	);
}

describe("useUser", () => {
	it("returns the signed-in user and their roles", async () => {
		render(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={meFetch()}>
				<Who />
			</BifrostProvider>,
		);
		expect(screen.getByTestId("who").textContent).toBe("loading");
		await waitFor(() =>
			expect(screen.getByTestId("who").textContent).toBe(
				"Pat|pat@example.test|org-1|false|true|false",
			),
		);
	});

	it("fetches the user once for every consumer under one provider", async () => {
		const fetchImpl = meFetch();
		render(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl}>
				<Who />
				<Who />
				<RequireRole role="Approvers">
					<span>approve</span>
				</RequireRole>
			</BifrostProvider>,
		);
		await screen.findByText("approve");
		expect(fetchImpl).toHaveBeenCalledTimes(1);
	});

	it("reports an error and no roles when the user can't be read", async () => {
		function Failure() {
			const user = useUser();
			return (
				<span data-testid="failure">
					{String(user.isLoading)}|{user.error ? "error" : "none"}|
					{String(user.hasRole("Approvers"))}
				</span>
			);
		}
		render(
			<BifrostProvider
				baseUrl="https://dev.example"
				token="t"
				fetchImpl={meFetch({ detail: "nope" }, 401)}
			>
				<Failure />
			</BifrostProvider>,
		);
		await waitFor(() =>
			expect(screen.getByTestId("failure").textContent).toBe("false|error|false"),
		);
	});
});

describe("useUser after a failed read", () => {
	it("asks again when the next consumer mounts", async () => {
		let status = 503;
		const fetchImpl = vi.fn(async () =>
			new Response(JSON.stringify(status === 200 ? ME : {}), { status }),
		) as unknown as typeof fetch & ReturnType<typeof vi.fn>;
		const { rerender } = render(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl}>
				<RequireRole role="Approvers" fallback={<span>denied</span>}>
					<span>approve</span>
				</RequireRole>
			</BifrostProvider>,
		);
		await screen.findByText("denied");
		status = 200;
		rerender(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl}>
				<RequireRole key="again" role="Approvers" fallback={<span>denied</span>}>
					<span>approve</span>
				</RequireRole>
			</BifrostProvider>,
		);
		await screen.findByText("approve");
		expect(fetchImpl).toHaveBeenCalledTimes(2);
	});
});

describe("RequireRole", () => {
	it("renders nothing while loading, then children for a holder", async () => {
		render(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={meFetch()}>
				<RequireRole role="Approvers" fallback={<span>denied</span>}>
					<span>approve</span>
				</RequireRole>
			</BifrostProvider>,
		);
		expect(screen.queryByText("approve")).toBeNull();
		expect(screen.queryByText("denied")).toBeNull();
		await screen.findByText("approve");
	});

	it("renders the fallback for someone without the role", async () => {
		render(
			<BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={meFetch()}>
				<RequireRole role="Admins" fallback={<span>denied</span>}>
					<span>settings</span>
				</RequireRole>
			</BifrostProvider>,
		);
		await screen.findByText("denied");
		expect(screen.queryByText("settings")).toBeNull();
	});
});
