import type {
	ServiceAttempt,
	ServiceListItem,
} from "@/services/services";

/**
 * Minimal live-shaped factories for Services UI tests.
 *
 * The Slice 3 `serviceFixtures.ts` mock is retired (2.2): pages read
 * `GET /api/services/*`, so tests build the same shapes inline instead
 * of importing shared mock data.
 */

export function makeService(
	overrides: Partial<ServiceListItem> & Pick<ServiceListItem, "id">,
): ServiceListItem {
	return {
		workflow_id: "00000000-0000-0000-0000-000000000000",
		workflow_name: "unnamed",
		workflow_path: "workflows/unnamed.py",
		organization_id: null,
		solution_id: null,
		enabled: true,
		startup_policy: "automatic",
		restart_policy: "always",
		desired_state: "running",
		blocked_reason: null,
		restart_eligible_at: null,
		current_revision: "abc1234",
		graceful_shutdown_seconds: 30,
		startup_grace_seconds: 60,
		restart_backoff_initial_seconds: 1,
		restart_backoff_max_seconds: 300,
		crash_loop_max_restarts: 5,
		crash_loop_window_seconds: 600,
		observed_state: "running",
		active_attempt_id: null,
		active_attempt: null,
		last_exit_reason: null,
		memory_mb: null,
		restart_count: 0,
		created_by: "dev@gobifrost.com",
		created_at: "2026-09-18T12:00:00Z",
		updated_at: "2026-09-20T08:00:00Z",
		...overrides,
	} as ServiceListItem;
}

export function makeAttempt(
	overrides: Partial<ServiceAttempt> & Pick<ServiceAttempt, "id">,
): ServiceAttempt {
	return {
		service_id: "00000000-0000-0000-0000-000000000000",
		revision: "abc1234",
		worker_id: null,
		state: "running",
		ready_at: null,
		started_at: "2026-09-20T08:00:00Z",
		heartbeat_at: "2026-09-20T08:05:00Z",
		stop_requested_at: null,
		stopped_at: null,
		exit_code: null,
		exit_reason: null,
		error: null,
		restart_number: 0,
		created_at: "2026-09-20T08:00:00Z",
		...overrides,
	} as ServiceAttempt;
}
