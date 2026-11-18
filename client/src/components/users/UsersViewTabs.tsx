import { WorkspaceTabs } from "@/components/layout/WorkspaceTabs";
import { useAuthorization } from "@/services/authorization";
import { IDENTITY_PROVIDERS_PATH } from "@/services/identity-providers";

/** Users | Identities | Identity Providers, the title of each list. */
export function UsersViewTabs() {
	const authorization = useAuthorization();
	return (
		<WorkspaceTabs
			label="User views"
			tabs={[
				{ to: "/users", label: "Users" },
				{ to: "/users/identities", label: "Identities" },
				...(authorization.canAnywhere("identityproviders.read")
					? [
							{
								to: IDENTITY_PROVIDERS_PATH,
								label: "Identity Providers",
							},
						]
					: []),
			]}
		/>
	);
}
