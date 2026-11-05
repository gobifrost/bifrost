import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { OrgScopeProvider, useOrgScope } from "./OrgScopeContext";
import {
	ACCESS_TOKEN_KEY,
	setImpersonationToken,
} from "@/lib/auth-token";

vi.mock("@/lib/branding", () => ({
	initializeBranding: vi.fn(),
	applyBrandingTheme: vi.fn(),
}));
afterEach(() => {
	vi.unstubAllGlobals();
	localStorage.clear();
	sessionStorage.clear();
});

beforeEach(() => {
	localStorage.clear();
	sessionStorage.clear();
});

function Draft() {
	const [value, setValue] = useState("");
	return (
		<input
			aria-label="Draft"
			value={value}
			onChange={(event) => setValue(event.target.value)}
		/>
	);
}
function Shell() {
	const { brandingLoaded, applicationName, refreshBranding } = useOrgScope();
	if (!brandingLoaded) return <p>Loading branding</p>;
	return (
		<>
			<span>{applicationName}</span>
			<button onClick={refreshBranding}>Refresh branding</button>
			<Draft />
		</>
	);
}
it("keeps the page draft mounted while refreshed branding loads", async () => {
	let finish!: (response: Response) => void;
	vi.stubGlobal(
		"fetch",
		vi
			.fn()
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ application_name: "Before" })),
			)
			.mockImplementationOnce(
				() =>
					new Promise<Response>((resolve) => {
						finish = resolve;
					}),
			),
	);
	render(
		<OrgScopeProvider>
			<Shell />
		</OrgScopeProvider>,
	);
	const draft = await screen.findByRole("textbox", { name: "Draft" });
	fireEvent.change(draft, { target: { value: "Unsaved work" } });
	fireEvent.click(screen.getByRole("button", { name: "Refresh branding" }));
	expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue(
		"Unsaved work",
	);
	expect(screen.queryByText("Loading branding")).not.toBeInTheDocument();
	await act(async () =>
		finish(new Response(JSON.stringify({ application_name: "After" }))),
	);
	expect(await screen.findByText("After")).toBeInTheDocument();
	expect(screen.getByRole("textbox", { name: "Draft" })).toHaveValue(
		"Unsaved work",
	);
});

it("uses the impersonated user's home organization without replacing the operator scope", async () => {
	const operatorScope = {
		type: "organization",
		orgId: "operator-org",
		orgName: "Operator",
	};
	localStorage.setItem(
		"msp-automation-org-scope",
		JSON.stringify(operatorScope),
	);
	localStorage.setItem(ACCESS_TOKEN_KEY, "operator-token");
	setImpersonationToken(
		`header.${btoa(JSON.stringify({ org_id: "target-org" }))}.signature`,
	);
	vi.stubGlobal(
		"fetch",
		vi.fn().mockResolvedValue(
			new Response(JSON.stringify({ application_name: "Bifrost" })),
		),
	);
	function ScopeProbe() {
		const { scope, setScope } = useOrgScope();
		return (
			<>
				<output>{scope.orgId}</output>
				<button
					onClick={() =>
						setScope({
							type: "organization",
							orgId: "other-org",
							orgName: "Other",
						})
					}
				>
					Change scope
				</button>
			</>
		);
	}
	render(
		<OrgScopeProvider>
			<ScopeProbe />
		</OrgScopeProvider>,
	);
	expect(await screen.findByText("target-org")).toBeInTheDocument();
	fireEvent.click(screen.getByRole("button", { name: "Change scope" }));
	expect(screen.getByText("target-org")).toBeInTheDocument();
	expect(JSON.parse(localStorage.getItem("msp-automation-org-scope")!)).toEqual(
		operatorScope,
	);
});
