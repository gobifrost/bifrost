import { describe, expect, it } from "vitest";
import {
	DEFAULT_BUILDER_WORKBENCH_STATE,
	loadBuilderWorkbenchState,
	saveBuilderWorkbenchState,
} from "./builder-workbench-state";

function memoryStorage(): Storage {
	const values = new Map<string, string>();
	return {
		get length() {
			return values.size;
		},
		clear: () => values.clear(),
		getItem: (key) => values.get(key) ?? null,
		key: (index) => [...values.keys()][index] ?? null,
		removeItem: (key) => {
			values.delete(key);
		},
		setItem: (key, value) => values.set(key, value),
	};
}

describe("builder workbench persistence", () => {
	it("round-trips the complete restorable state per Solution", () => {
		const storage = memoryStorage();
		const state = {
			activeSessionId: "session-2",
			workbenchTab: "changes" as const,
			mobilePane: "changes" as const,
			agentPanelWidth: 48,
			previewRoute: "/reports/quarterly",
			previewDevice: "tablet" as const,
		};

		saveBuilderWorkbenchState("solution-1", state, storage);

		expect(loadBuilderWorkbenchState("solution-1", storage)).toEqual(state);
		expect(loadBuilderWorkbenchState("solution-2", storage)).toEqual(
			DEFAULT_BUILDER_WORKBENCH_STATE,
		);
	});

	it("falls back safely when persisted data is corrupt", () => {
		const storage = memoryStorage();
		storage.setItem("bifrost:builder-workbench:v1:solution-1", "{broken");

		expect(loadBuilderWorkbenchState("solution-1", storage)).toEqual(
			DEFAULT_BUILDER_WORKBENCH_STATE,
		);
	});

	it("validates routes and clamps the restored split", () => {
		const storage = memoryStorage();
		storage.setItem(
			"bifrost:builder-workbench:v1:solution-1",
			JSON.stringify({
				workbenchTab: "unknown",
				mobilePane: "unknown",
				previewDevice: "watch",
				previewRoute: "not-absolute",
				agentPanelWidth: 200,
			}),
		);

		expect(loadBuilderWorkbenchState("solution-1", storage)).toEqual({
			...DEFAULT_BUILDER_WORKBENCH_STATE,
			agentPanelWidth: 58,
		});
	});
});
