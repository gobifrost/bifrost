export function getAppLoadErrorMessage(status: number): string {
	switch (status) {
		case 401:
			return "Sign in again to open this app.";
		case 403:
			return "You do not have permission to open this app.";
		case 404:
			return "This app is unavailable.";
		default:
			return "The app could not be loaded. Please try again.";
	}
}
