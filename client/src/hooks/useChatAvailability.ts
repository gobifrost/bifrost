import { useQuery } from "@tanstack/react-query";

import { getChatModelProfiles } from "@/services/chatModels";
import { useUserPermissions } from "@/hooks/useUserPermissions";

export function useChatAvailability() {
	const { isPlatformAdmin, isLoading: permissionsLoading } = useUserPermissions();
	const profilesQuery = useQuery({
		queryKey: ["chat", "model-profiles"],
		queryFn: getChatModelProfiles,
		staleTime: 5 * 60 * 1000,
		retry: false,
	});

	return {
		isConfigured: (profilesQuery.data?.profiles.length ?? 0) > 0,
		isPlatformAdmin,
		isLoading: permissionsLoading || profilesQuery.isLoading,
		error: profilesQuery.error,
	};
}
