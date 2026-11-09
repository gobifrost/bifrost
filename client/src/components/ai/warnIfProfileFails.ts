import { toast } from "sonner";

import { verifyModelProfile, type AIModelProfileVerifyResponse } from "@/services/aiModels";

/**
 * Tests a just-saved profile with one real request and reports failures or usage warnings.
 * The save already succeeded; a failure does not undo it.
 */
export async function warnIfProfileFails(
	profileId: string,
	profileName: string,
): Promise<AIModelProfileVerifyResponse | undefined> {
	try {
		const result = await verifyModelProfile(profileId);
		if (!result.success) {
			toast.warning(`${profileName} could not answer a test request`, {
				description: result.message,
			});
		} else if (result.warnings?.length) {
			toast.warning(`${profileName} answered with a usage warning`, {
				description: result.warnings.join(" "),
			});
		}
		return result;
	} catch (error) {
		toast.warning(`${profileName} could not be tested`, {
			description: error instanceof Error ? error.message : undefined,
		});
		return undefined;
	}
}
