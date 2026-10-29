import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SchedulerDiagnosticsResponse } from "@/services/schedulerDiagnostics";
import { SchedulerReplicaList } from "./SchedulerReplicaList";

type Replica = SchedulerDiagnosticsResponse["replicas"][number];

function replica(overrides: Partial<Replica>): Replica {
	return {
		id: "replica",
		hostname: "replica",
		pid: 1,
		runs_platform_jobs: false,
		is_leader: false,
		online: true,
		started_at: "2026-10-24T17:00:00Z",
		last_heartbeat_at: "2026-10-24T17:10:00Z",
		memory_current_bytes: 400 * 2 ** 20,
		memory_limit_bytes: 2 ** 30,
		active_platform_job_ids: [],
		active_platform_jobs: 0,
		...overrides,
	};
}

describe("SchedulerReplicaList", () => {
	it("labels the trigger leader, control replicas and job runners by what they do", () => {
		render(
			<SchedulerReplicaList
				replicas={[
					replica({
						id: "leader",
						hostname: "api-a",
						is_leader: true,
					}),
					replica({ id: "control", hostname: "api-b" }),
					replica({
						id: "worker",
						hostname: "worker-job",
						runs_platform_jobs: true,
					}),
				]}
			/>,
		);

		const table = screen.getByRole("table");
		const roleOf = (hostname: string) =>
			within(
				within(table).getByText(hostname).closest("tr") as HTMLElement,
			).getAllByRole("cell")[1].textContent;
		expect(roleOf("api-a")).toBe("Trigger Leader");
		expect(roleOf("api-b")).toBe("Control");
		expect(roleOf("worker-job")).toBe("Job Runner");
	});
});
