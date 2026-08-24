export function routeRevealKey(pathname: string, locationKey: string): string {
	if (pathname === "/settings" || pathname.startsWith("/settings/")) {
		return "settings";
	}

	const isAppRoute = /^\/apps\/[^/]+(?:\/|$)/.test(pathname);
	const isAppEditorRoute = /^\/apps\/[^/]+\/edit(?:\/|$)/.test(pathname);
	const isAppRunnerRoute = isAppRoute && !isAppEditorRoute;
	return isAppRunnerRoute ? "app-runner" : locationKey;
}
