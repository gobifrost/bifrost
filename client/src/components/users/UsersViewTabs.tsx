import { WorkspaceTabs } from "@/components/layout/WorkspaceTabs";

/** Users | Identities, the title of both lists. */
export function UsersViewTabs() {
	return (
		<WorkspaceTabs
			label="User views"
			tabs={[
				{ to: "/users", label: "Users" },
				{ to: "/users/identities", label: "Identities" },
			]}
		/>
	);
}
