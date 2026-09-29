import { toast } from "sonner";

import { verifyModelProfile } from "@/services/aiModels";

/**
 * Tests a just-saved profile with one real request and warns if it fails.
 * The save already succeeded; a failure does not undo it.
 */
export async function warnIfProfileFails(
	profileId: string,
	profileName: string,
): Promise<void> {
	try {
		const result = await verifyModelProfile(profileId);
		if (!result.success) {
			toast.warning(`${profileName} could not answer a test request`, {
				description: result.message,
			});
		}
	} catch (error) {
		toast.warning(`${profileName} could not be tested`, {
			description: error instanceof Error ? error.message : undefined,
		});
	}
}
