import { afterEach, describe, expect, it } from "vitest";

import { motionSeconds } from "./motion";

describe("motionSeconds", () => {
	afterEach(() => {
		document.documentElement.style.removeProperty("--bf-motion-disclosure");
	});

	it("reads a motion token in seconds", () => {
		document.documentElement.style.setProperty(
			"--bf-motion-disclosure",
			"220ms",
		);

		expect(motionSeconds("--bf-motion-disclosure")).toBe(0.22);
	});

	it("follows the token to zero under reduced motion", () => {
		document.documentElement.style.setProperty(
			"--bf-motion-disclosure",
			"0ms",
		);

		expect(motionSeconds("--bf-motion-disclosure")).toBe(0);
	});

	it("is zero when the token is not defined", () => {
		expect(motionSeconds("--bf-motion-disclosure")).toBe(0);
	});
});
