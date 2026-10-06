import { describe, expect, it } from "vitest";

import {
	actionWord,
	permissionActionWord,
	permissionParts,
} from "./permission-words";

describe("permissionParts", () => {
	it("splits a dotted domain from its action", () => {
		expect(permissionParts("users.lifecycle.readwrite")).toEqual({
			domain: "users.lifecycle",
			action: "readwrite",
			all: false,
		});
	});

	it("keeps the .all extension apart from the action", () => {
		expect(permissionParts("agentruns.read.all")).toEqual({
			domain: "agentruns",
			action: "read",
			all: true,
		});
	});
});

describe("actionWord", () => {
	it("says view, manage and run instead of read, readwrite and execute", () => {
		expect(actionWord("read")).toBe("view");
		expect(actionWord("readwrite")).toBe("manage");
		expect(actionWord("execute")).toBe("run");
	});

	it("keeps the meaning of .all", () => {
		expect(actionWord("read.all")).toBe("view all");
		expect(actionWord("readwrite.all")).toBe("manage all");
	});
});

describe("permissionActionWord", () => {
	it("words the action of a whole permission", () => {
		expect(permissionActionWord("roles.readwrite")).toBe("manage");
		expect(permissionActionWord("workflows.execute")).toBe("run");
		expect(permissionActionWord("agentruns.read.all")).toBe("view all");
	});
});
