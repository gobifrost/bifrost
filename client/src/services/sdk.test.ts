import { describe, expect, it } from "vitest";

import { getSdkDownloadUrl } from "./sdk";

describe("getSdkDownloadUrl", () => {
	it("returns a Python artifact URL that uv can classify before downloading", () => {
		expect(getSdkDownloadUrl()).toBe(
			"/api/cli/download/bifrost-cli.tar.gz",
		);
	});
});
