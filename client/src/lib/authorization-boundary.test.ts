import { beforeEach, describe, expect, it } from "vitest";
import {
	AUTHORIZATION_BOUNDARY_CHANGED_EVENT,
	authorizationBoundaryStorageKey,
	getSelectedAuthorizationBoundary,
	storeSelectedAuthorizationBoundary,
} from "./authorization-boundary";

describe("authorization boundary storage", () => {
	beforeEach(() => sessionStorage.clear());

	it("keeps selections isolated by signed-in user", () => {
		storeSelectedAuthorizationBoundary("user-one", "organization:one");
		storeSelectedAuthorizationBoundary("user-two", "platform");

		expect(getSelectedAuthorizationBoundary("user-one")).toBe(
			"organization:one",
		);
		expect(getSelectedAuthorizationBoundary("user-two")).toBe("platform");
	});

	it("resolves the active user's selection for shared API clients", () => {
		sessionStorage.setItem("userId", "user-one");
		sessionStorage.setItem(
			authorizationBoundaryStorageKey("user-one"),
			"managed_organizations",
		);

		expect(getSelectedAuthorizationBoundary()).toBe(
			"managed_organizations",
		);
	});

	it("emits a tab-local event when the selected boundary changes", () => {
		const events: Array<{ userId: string; boundary: string }> = [];
		window.addEventListener(AUTHORIZATION_BOUNDARY_CHANGED_EVENT, (event) => {
			events.push(
				(event as CustomEvent<{ userId: string; boundary: string }>)
					.detail,
			);
		});

		storeSelectedAuthorizationBoundary("user-one", "platform");

		expect(events).toEqual([{ userId: "user-one", boundary: "platform" }]);
	});
});
