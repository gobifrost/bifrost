import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useWorkerInstances } from "@/services/worker-instances";
import { WorkerInstances } from "./WorkerInstances";

vi.mock("@/services/worker-instances", () => ({ useWorkerInstances: vi.fn() }));

it("shows the stored build and reason for a stopped worker and requests the next page", async () => {
	const cursor = {
		before_id: "47fb9102-3944-4139-baa6-3f90556bb334",
		before_created_at: "2026-11-27T00:00:00Z",
	};
	vi.mocked(useWorkerInstances).mockReturnValue({
		data: {
			items: [
				{
					id: cursor.before_id,
					form: "external",
					state: "lost",
					capacity_scope: "cloudflare-test",
					capacity_resource_uid: "test-resource",
					version: "v2.0.1",
					created_at: cursor.before_created_at,
					ended_at: "2026-11-27T00:02:00Z",
					end_reason: "heartbeat_lost",
				},
			],
			next_cursor: cursor,
		},
		isPending: false,
		isFetching: false,
	} as ReturnType<typeof useWorkerInstances>);
	render(<WorkerInstances />);

	const row = screen.getByRole("row", { name: /cloudflare-test/ });
	expect(row).toHaveTextContent("Heartbeat lost");
	expect(row).toHaveTextContent("v2.0.1");
	await userEvent.click(screen.getByRole("button", { name: "Next" }));
	expect(useWorkerInstances).toHaveBeenLastCalledWith(cursor);
});
