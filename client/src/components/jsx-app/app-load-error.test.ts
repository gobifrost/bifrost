import { describe, expect, it } from "vitest";
import { getAppLoadErrorMessage } from "./app-load-error";

describe("getAppLoadErrorMessage", () => {
	it.each([
		[401, "Sign in again to open this app."],
		[403, "You do not have permission to open this app."],
		[404, "This app is unavailable."],
		[500, "The app could not be loaded. Please try again."],
	])("maps status %i to a safe app-load message", (status, message) => {
		expect(getAppLoadErrorMessage(status)).toBe(message);
	});
});
