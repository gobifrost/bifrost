import { beforeEach, describe, expect, it } from "vitest";
import { useExecutionStreamStore } from "./executionStreamStore";

describe("execution stream queue advisories", () => {
	beforeEach(() => {
		useExecutionStreamStore.setState({ streams: {} });
	});

	it("initializes an unconfirmed stream as pending and confirms it from a queue advisory", () => {
		const store = useExecutionStreamStore.getState();
		store.startStreaming("execution-1");

		store.updateQueuePosition("execution-1", {
			queuePosition: 3,
			waitReason: "queued",
		});

		expect(useExecutionStreamStore.getState().streams["execution-1"]).toMatchObject({
			status: "Pending",
			hasReceivedUpdate: true,
			queuePosition: 3,
			waitReason: "queued",
		});
	});

	it("updates only queue metadata while pending", () => {
		const store = useExecutionStreamStore.getState();
		store.startStreaming("execution-1", "Pending");
		store.updateStatus("execution-1", "Pending", {
			queuePosition: 4,
			waitReason: "queued",
			availableMemoryMb: 120,
			requiredMemoryMb: 64,
		});

		store.updateQueuePosition("execution-1", {
			queuePosition: 2,
			waitReason: "queued",
		});

		expect(useExecutionStreamStore.getState().streams["execution-1"]).toMatchObject({
			status: "Pending",
			hasReceivedUpdate: true,
			queuePosition: 2,
			waitReason: "queued",
			availableMemoryMb: 120,
			requiredMemoryMb: 64,
		});
	});

	it.each(["Running", "Cancelling", "Success"] as const)(
		"does not regress an authoritative %s update",
		(status) => {
			const store = useExecutionStreamStore.getState();
			store.startStreaming("execution-1");
			store.updateStatus("execution-1", status);
			const beforeAdvisory = useExecutionStreamStore.getState().streams["execution-1"];

			store.updateQueuePosition("execution-1", {
				queuePosition: 1,
				waitReason: "queued",
			});

			expect(useExecutionStreamStore.getState().streams["execution-1"]).toEqual(
				beforeAdvisory,
			);
		},
	);

	it("does not regress an execution completed before a status update", () => {
		const store = useExecutionStreamStore.getState();
		store.startStreaming("execution-1");
		store.completeExecution("execution-1", undefined, "Success");
		const beforeAdvisory = useExecutionStreamStore.getState().streams["execution-1"];

		store.updateQueuePosition("execution-1", {
			queuePosition: 1,
			waitReason: "queued",
		});

		expect(useExecutionStreamStore.getState().streams["execution-1"]).toEqual(
			beforeAdvisory,
		);
	});

	it("accepts an authoritative pending handback after running", () => {
		const store = useExecutionStreamStore.getState();
		store.startStreaming("execution-1");
		store.updateStatus("execution-1", "Running");

		store.updateStatus("execution-1", "Pending", {
			queuePosition: 1,
			waitReason: "queued",
		});

		expect(useExecutionStreamStore.getState().streams["execution-1"]).toMatchObject({
			status: "Pending",
			hasReceivedUpdate: true,
			queuePosition: 1,
			waitReason: "queued",
		});
	});
});
