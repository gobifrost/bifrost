import { type ComponentProps } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/filePolicies", () => ({
	effectiveAccess: vi.fn(),
}));
vi.mock("@/services/policyRules", () => ({
	listPolicyRules: vi.fn(),
}));
vi.mock("@/components/solutions/SolutionManagedBadge", () => ({
	SolutionManagedBadge: () => (
		<span data-testid="solution-managed-badge">Managed</span>
	),
}));
import { effectiveAccess } from "@/services/filePolicies";
import { listPolicyRules } from "@/services/policyRules";
import { EffectiveAccessPanel } from "./EffectiveAccessPanel";

describe("EffectiveAccessPanel", () => {
	beforeEach(() => {
		vi.mocked(effectiveAccess).mockReset();
		vi.mocked(listPolicyRules).mockReset();
		vi.mocked(listPolicyRules).mockResolvedValue([]);
	});

	function renderPanel(props: ComponentProps<typeof EffectiveAccessPanel>) {
		const queryClient = new QueryClient({
			defaultOptions: {
				queries: {
					retry: false,
				},
			},
		});
		return {
			...render(
				<QueryClientProvider client={queryClient}>
					<EffectiveAccessPanel {...props} />
				</QueryClientProvider>,
			),
			queryClient,
		};
	}

	it("renders the governing policy and inherited fallbacks in readable terms", async () => {
		vi.mocked(listPolicyRules).mockResolvedValue([
			{
				id: "rule-1",
				name: "admin_bypass",
				domain: "file",
				description:
					"Platform Admin Access bypasses all file checks. Built-in, read-only.",
				body: {
					actions: ["read", "write", "delete", "list"],
					when: { authority: "platform_admin" },
				},
				is_builtin: true,
				organization_id: null,
				created_at: "2026-01-01T00:00:00Z",
				updated_at: "2026-01-01T00:00:00Z",
			},
		]);
		vi.mocked(effectiveAccess).mockResolvedValue([
			{
				id: "2",
				location: "gallery",
				path: "team/",
				policies: {
					policies: [{ $ref: "admin_bypass" }],
				},
			},
			{
				id: "1",
				location: "gallery",
				path: "",
				policies: {
					policies: [
						{ name: "root-rule", actions: ["read", "list"] },
					],
				},
			},
		]);
		renderPanel({
			location: "gallery",
			scope: null,
			path: "team/pic.png",
			onOpenTest: vi.fn(),
			onManagePolicy: vi.fn(),
		});
		await waitFor(() =>
			expect(screen.getByText("Platform Admin Access")).toBeInTheDocument(),
		);
		expect(screen.getByText("Root Rule")).toBeInTheDocument();
		expect(screen.getByText("Inherited Access")).toBeInTheDocument();
		expect(screen.getByText("From share root")).toBeInTheDocument();
		expect(screen.getByText("Named rule")).toBeInTheDocument();
		expect(await screen.findByText("Write")).toBeInTheDocument();
		expect(
			screen.getByText(/Only this nearest matching policy is used/),
		).toBeInTheDocument();
		expect(vi.mocked(listPolicyRules)).toHaveBeenCalledWith("file");
	});

	it("refreshes when the target identity changes", async () => {
		vi.mocked(effectiveAccess)
			.mockResolvedValueOnce([
				{
					id: "1",
					location: "gallery",
					path: "team/a/",
					policies: {
						policies: [{ name: "alpha-rule", actions: ["read"] }],
					},
				},
			])
			.mockResolvedValueOnce([
				{
					id: "2",
					location: "gallery",
					path: "team/b/",
					policies: {
						policies: [{ name: "beta-rule", actions: ["write"] }],
					},
				},
			]);
		const { rerender, queryClient } = renderPanel({
			location: "gallery",
			scope: null,
			path: "team/a/file.txt",
			onOpenTest: vi.fn(),
			onManagePolicy: vi.fn(),
		});
		await waitFor(() =>
			expect(screen.getByText("Alpha Rule")).toBeInTheDocument(),
		);
		rerender(
			<QueryClientProvider client={queryClient}>
				<EffectiveAccessPanel
					location="gallery"
					scope={null}
					path="team/b/file.txt"
					onOpenTest={vi.fn()}
					onManagePolicy={vi.fn()}
				/>
			</QueryClientProvider>,
		);
		await waitFor(() =>
			expect(screen.getByText("Beta Rule")).toBeInTheDocument(),
		);
		expect(screen.queryByText("Alpha Rule")).not.toBeInTheDocument();
	});

	it("retries a failed access lookup", async () => {
		vi.mocked(effectiveAccess)
			.mockRejectedValueOnce(new Error("lookup failed"))
			.mockResolvedValueOnce([
				{
					id: "3",
					location: "gallery",
					path: "team/c/",
					policies: {
						policies: [
							{ name: "recovered-rule", actions: ["read"] },
						],
					},
				},
			]);
		renderPanel({
			location: "gallery",
			scope: null,
			path: "team/c/file.txt",
			onOpenTest: vi.fn(),
			onManagePolicy: vi.fn(),
		});
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Couldn’t resolve access",
		);
		fireEvent.click(screen.getByRole("button", { name: /retry access/i }));
		await waitFor(() =>
			expect(screen.getByText("Recovered Rule")).toBeInTheDocument(),
		);
	});

	it("opens the governing policy source when a callback is provided", async () => {
		const policy = {
			id: "4",
			location: "gallery",
			path: "team/",
			policies: {
				policies: [{ name: "team-rule", actions: ["read" as const] }],
			},
		};
		vi.mocked(effectiveAccess).mockResolvedValue([policy]);
		const onOpenPolicy = vi.fn();
		renderPanel({
			location: "gallery",
			scope: null,
			path: "team/pic.png",
			onOpenTest: vi.fn(),
			onManagePolicy: vi.fn(),
			onOpenPolicy,
		});

		fireEvent.click(
			await screen.findByRole("button", { name: /open source/i }),
		);

		expect(onOpenPolicy).toHaveBeenCalledWith(policy);
	});

	it("fires onOpenTest when Test access is clicked", async () => {
		vi.mocked(effectiveAccess).mockResolvedValue([]);
		const onOpenTest = vi.fn();
		renderPanel({
			location: "gallery",
			scope: null,
			path: "pic.png",
			onOpenTest,
			onManagePolicy: vi.fn(),
		});
		fireEvent.click(screen.getByRole("button", { name: "Test Access" }));
		expect(onOpenTest).toHaveBeenCalled();
	});

	it("hides policy management in read-only solution file scope", async () => {
		vi.mocked(effectiveAccess).mockResolvedValue([]);
		const onManagePolicy = vi.fn();
		renderPanel({
			location: "reports",
			scope: "sol-1",
			path: "demo/readme.txt",
			readOnly: true,
			managedBySolution: true,
			solutionId: "sol-1",
			onOpenTest: vi.fn(),
			onManagePolicy,
		});

		expect(
			screen.queryByRole("button", { name: "Manage Policy" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByTestId("solution-managed-badge"),
		).toBeInTheDocument();
		expect(onManagePolicy).not.toHaveBeenCalled();
	});
});
