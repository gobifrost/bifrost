import { GLOBAL_TARGET } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import { VersionUpdateBanner } from "./VersionUpdateBanner";
import { FileActivityIndicator } from "./FileActivityIndicator";

export function HeaderStatusIndicators() {
	const authorization = useAuthorization();
	const canReadActivity = authorization.canAt("platform.read", GLOBAL_TARGET);

	return (
		<>
			<VersionUpdateBanner />
			{canReadActivity && <FileActivityIndicator />}
		</>
	);
}
