import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { FormListSurface, type FormListItem } from "./FormListSurface";

const form = {
	id: "form-1",
	name: "Dispatch Intake",
	description: "Coordinate field work",
	is_active: true,
	organization_id: null,
	logo_url: null,
	logo_version: null,
} as FormListItem;

function validation(valid = true) {
	return new Map([
		[
			form.id,
			{
				valid,
				missingParams: valid ? [] : ["request_id"],
			},
		],
	]);
}

describe("FormListSurface", () => {
	it("opens the form runner from the grid card primary target", async () => {
		const onLaunch = vi.fn();
		const { user } = renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="grid"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation()}
				onLaunch={onLaunch}
			/>,
		);

		const title = screen.getByRole("link", { name: "Dispatch Intake" });
		expect(title).toHaveAttribute("href", "/execute/form-1");

		await user.click(title);

		expect(onLaunch).toHaveBeenCalledExactlyOnceWith(form);
	});

	it("keeps management overflow actions separate from grid launch", async () => {
		const onLaunch = vi.fn();
		const onEdit = vi.fn();
		const { user } = renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="grid"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation()}
				onLaunch={onLaunch}
				onEdit={onEdit}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Dispatch Intake actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Edit Form" }));

		expect(onEdit).toHaveBeenCalledExactlyOnceWith(form);
		expect(onLaunch).not.toHaveBeenCalled();
	});

	it("opens the form runner from the table row without editing", async () => {
		const onLaunch = vi.fn();
		const onEdit = vi.fn();
		const { user } = renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="table"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation()}
				onLaunch={onLaunch}
				onEdit={onEdit}
			/>,
		);

		const title = screen.getByRole("link", { name: "Dispatch Intake" });
		expect(title).toHaveAttribute("href", "/execute/form-1");
		expect(
			screen.queryByRole("button", { name: "Launch form" }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("row", { name: /Dispatch Intake/i }));

		expect(onLaunch).toHaveBeenCalledExactlyOnceWith(form);
		expect(onEdit).not.toHaveBeenCalled();
	});

	it("does not expose a table launch target before validation is available", async () => {
		renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="table"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={new Map()}
				onLaunch={vi.fn()}
				onEdit={vi.fn()}
			/>,
		);

		expect(
			screen.queryByRole("link", { name: "Dispatch Intake" }),
		).not.toBeInTheDocument();
	});

	it("explains invalid table titles without exposing a launch link", async () => {
		renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="table"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation(false)}
				onLaunch={vi.fn()}
				onEdit={vi.fn()}
			/>,
		);

		expect(
			screen.queryByRole("link", { name: "Dispatch Intake" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByTitle("Cannot launch: Missing request_id"),
		).toHaveTextContent("Dispatch Intake");
	});

	it("disables invalid grid cards while leaving admin actions available", async () => {
		const onLaunch = vi.fn();
		const onEdit = vi.fn();
		const { user } = renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode="grid"
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation(false)}
				onLaunch={onLaunch}
				onEdit={onEdit}
			/>,
		);

		expect(
			screen.queryByRole("link", { name: "Dispatch Intake" }),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Dispatch Intake actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Edit Form" }));

		expect(onLaunch).not.toHaveBeenCalled();
		expect(onEdit).toHaveBeenCalledExactlyOnceWith(form);
	});
});

it.each(["grid", "table"] as const)(
	"preserves solution context in %s links",
	(viewMode) => {
		renderWithProviders(
			<FormListSurface
				forms={[form]}
				viewMode={viewMode}
				isPlatformAdmin
				canManageForms
				getOrgName={() => "Global"}
				formValidation={validation()}
				onLaunch={vi.fn()}
				navigationSearch="?from=solution:install-1"
			/>,
		);
		expect(
			screen.getByRole("link", { name: "Dispatch Intake" }),
		).toHaveAttribute("href", "/execute/form-1?from=solution:install-1");
	},
);
