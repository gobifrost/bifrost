import { beforeEach, describe, expect, it, vi } from "vitest";

const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	detachAgentSkill,
	downloadAgentSkill,
	getAgentSkill,
	getAgentSkillFile,
	uploadAgentSkill,
} from "./agentSkills";

beforeEach(() => {
	mockAuthFetch.mockReset();
});

describe("Agent Skills service", () => {
	it("loads the portable skill projection", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () =>
				Promise.resolve({
					name: "Ticket triage",
					description: "Routes tickets",
					bundle_path: null,
					skill_markdown: "---\nname: ticket-triage\n---",
					files: ["SKILL.md"],
					companion_files: [],
					automatic_capabilities: [],
					source: "inline",
					is_managed: false,
				}),
		});

		const skill = await getAgentSkill("agent-1");

		expect(mockAuthFetch).toHaveBeenCalledWith("/api/agents/agent-1/skill", {
			signal: undefined,
		});
		expect(skill.skill_markdown).toContain("ticket-triage");
	});

	it("reads a bundle file with an encoded path", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () =>
				Promise.resolve({
					path: "references/run book.md",
					encoding: "utf-8",
					content: "Guide",
				}),
		});

		const result = await getAgentSkillFile(
			"agent-1",
			"references/run book.md",
		);

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/agents/agent-1/skill/file?path=references%2Frun+book.md",
			{ signal: undefined },
		);
		expect(result.content).toBe("Guide");
	});

	it("uploads and detaches a portable bundle", async () => {
		const uploaded = {
			name: "ticket-triage",
			bundle_path: "skills/ticket-triage",
		};
		mockAuthFetch
			.mockResolvedValueOnce({
				ok: true,
				json: () => Promise.resolve(uploaded),
			})
			.mockResolvedValueOnce({ ok: true });
		const archive = new File(["zip"], "ticket-triage.skill");

		await expect(uploadAgentSkill("agent-1", archive)).resolves.toEqual(
			uploaded,
		);
		const uploadCall = mockAuthFetch.mock.calls[0];
		expect(uploadCall[0]).toBe("/api/agents/agent-1/skill/bundle");
		expect(uploadCall[1].method).toBe("PUT");
		expect(uploadCall[1].body).toBeInstanceOf(FormData);

		await expect(detachAgentSkill("agent-1")).resolves.toBeUndefined();
		expect(mockAuthFetch).toHaveBeenLastCalledWith(
			"/api/agents/agent-1/skill/bundle",
			{ method: "DELETE" },
		);
	});

	it("downloads the export using the server filename", async () => {
		const blob = new Blob(["zip"]);
		mockAuthFetch.mockResolvedValue({
			ok: true,
			headers: new Headers({
				"Content-Disposition": 'attachment; filename="ticket-triage.zip"',
			}),
			blob: () => Promise.resolve(blob),
		});

		const result = await downloadAgentSkill("agent-1");

		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/agents/agent-1/skill/download",
			{ signal: undefined },
		);
		expect(result).toEqual({ blob, filename: "ticket-triage.zip" });
	});

	it("surfaces an API detail when an export fails", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: false,
			json: () => Promise.resolve({ detail: "Bundle path is unavailable" }),
		});

		await expect(downloadAgentSkill("agent-1")).rejects.toThrow(
			"Bundle path is unavailable",
		);
	});
});
