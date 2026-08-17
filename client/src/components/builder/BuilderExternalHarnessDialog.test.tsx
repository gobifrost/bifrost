// @vitest-environment happy-dom

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BuilderSession } from "@/services/builder";
import { BuilderExternalHarnessDialog } from "./BuilderExternalHarnessDialog";

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

const session: BuilderSession = {
	id: "11111111-1111-4111-8111-111111111111",
	solution_id: "22222222-2222-4222-8222-222222222222",
	conversation_id: "33333333-3333-4333-8333-333333333333",
	user_id: "44444444-4444-4444-8444-444444444444",
	builder_agent_id: "55555555-5555-4555-8555-555555555555",
	created_at: "2026-08-16T00:00:00Z",
	updated_at: "2026-08-16T00:00:00Z",
};

describe("BuilderExternalHarnessDialog", () => {
	beforeEach(() => {
		Object.defineProperty(navigator, "clipboard", {
			configurable: true,
			value: { writeText: vi.fn().mockResolvedValue(undefined) },
		});
	});

	it("shows the shared MCP, agent, and session coordinates", () => {
		render(<BuilderExternalHarnessDialog session={session} />);

		fireEvent.click(
			screen.getByRole("button", { name: /use your ai/i }),
		);

		expect(screen.getByText(`${window.location.origin}/mcp`)).toBeInTheDocument();
		expect(screen.getByText(session.builder_agent_id)).toBeInTheDocument();
		expect(screen.getByText(session.id)).toBeInTheDocument();
		expect(screen.getByText(/finalize only the last edit/i)).toBeInTheDocument();
	});

	it("requires a native Builder session before handing off", () => {
		render(<BuilderExternalHarnessDialog session={undefined} />);

		expect(
			screen.getByRole("button", { name: /use your ai/i }),
		).toBeDisabled();
	});
});
