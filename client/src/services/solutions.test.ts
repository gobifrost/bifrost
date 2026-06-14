import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPatch = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: (...args: unknown[]) => mockGet(...args),
		PATCH: (...args: unknown[]) => mockPatch(...args),
		PUT: (...args: unknown[]) => mockPut(...args),
		DELETE: (...args: unknown[]) => mockDelete(...args),
	},
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

import {
	deleteSolution,
	getSolution,
	getSolutionEntities,
	getSolutionReadme,
	installSolution,
	listSolutions,
	previewInstall,
	putSolutionReadme,
	updateSolution,
} from "./solutions";

beforeEach(() => {
	mockGet.mockReset();
	mockPatch.mockReset();
	mockPut.mockReset();
	mockDelete.mockReset();
	mockAuthFetch.mockReset();
});

describe("solutions service", () => {
	it("lists solutions", async () => {
		mockGet.mockResolvedValue({ data: { solutions: [] } });

		const out = await listSolutions();

		expect(mockGet).toHaveBeenCalledWith("/api/solutions", {});
		expect(out).toEqual({ solutions: [] });
	});

	it("gets a solution by id", async () => {
		mockGet.mockResolvedValue({ data: { id: "sol-1", slug: "demo" } });

		const out = await getSolution("sol-1");

		expect(mockGet).toHaveBeenCalledWith("/api/solutions/{solution_id}", {
			params: { path: { solution_id: "sol-1" } },
		});
		expect(out.id).toBe("sol-1");
	});

	it("gets solution entities by id", async () => {
		mockGet.mockResolvedValue({
			data: { solution: { id: "sol-1" }, tables: [], configs: [] },
		});

		const out = await getSolutionEntities("sol-1");

		expect(mockGet).toHaveBeenCalledWith(
			"/api/solutions/{solution_id}/entities",
			{ params: { path: { solution_id: "sol-1" } } },
		);
		expect(out.solution.id).toBe("sol-1");
	});

	it("gets a solution readme by id", async () => {
		mockGet.mockResolvedValue({ data: { readme: "# Hello" } });

		const out = await getSolutionReadme("sol-1");

		expect(mockGet).toHaveBeenCalledWith(
			"/api/solutions/{solution_id}/readme",
			{ params: { path: { solution_id: "sol-1" } } },
		);
		expect(out.readme).toBe("# Hello");
	});

	it("puts a solution readme with the body", async () => {
		mockPut.mockResolvedValue({ data: { readme: "# Updated" } });

		const out = await putSolutionReadme("sol-1", "# Updated");

		expect(mockPut).toHaveBeenCalledWith(
			"/api/solutions/{solution_id}/readme",
			{
				params: { path: { solution_id: "sol-1" } },
				body: { readme: "# Updated" },
			},
		);
		expect(out.readme).toBe("# Updated");
	});

	it("clears a solution readme with null", async () => {
		mockPut.mockResolvedValue({ data: { readme: null } });

		const out = await putSolutionReadme("sol-1", null);

		expect(mockPut).toHaveBeenCalledWith(
			"/api/solutions/{solution_id}/readme",
			{
				params: { path: { solution_id: "sol-1" } },
				body: { readme: null },
			},
		);
		expect(out.readme).toBeNull();
	});

	it("updates a solution by id with the body", async () => {
		mockPatch.mockResolvedValue({ data: { id: "sol-1", name: "Renamed" } });

		await updateSolution("sol-1", { name: "Renamed" });

		expect(mockPatch).toHaveBeenCalledWith("/api/solutions/{solution_id}", {
			params: { path: { solution_id: "sol-1" } },
			body: { name: "Renamed" },
		});
	});

	it("deletes a solution by id", async () => {
		mockDelete.mockResolvedValue({ data: { solution_id: "sol-1" } });

		const out = await deleteSolution("sol-1");

		expect(mockDelete).toHaveBeenCalledWith("/api/solutions/{solution_id}", {
			params: { path: { solution_id: "sol-1" } },
		});
		expect(out.solution_id).toBe("sol-1");
	});

	it("forwards an AbortSignal", async () => {
		mockGet.mockResolvedValue({ data: { solutions: [] } });
		const controller = new AbortController();

		await listSolutions({ signal: controller.signal });

		expect(mockGet).toHaveBeenCalledWith("/api/solutions", {
			signal: controller.signal,
		});
	});

	it("throws on API errors", async () => {
		mockGet.mockResolvedValue({ error: { detail: "boom" } });

		await expect(listSolutions()).rejects.toThrow(/boom/);
	});

	it("previews an install with a multipart file", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ slug: "demo", tables: [] }),
		});
		const file = new File(["zip-bytes"], "demo.zip", {
			type: "application/zip",
		});

		const out = await previewInstall(file);

		expect(mockAuthFetch).toHaveBeenCalledTimes(1);
		const [url, init] = mockAuthFetch.mock.calls[0];
		expect(url).toBe("/api/solutions/install/preview");
		expect(init.method).toBe("POST");
		expect(init.body).toBeInstanceOf(FormData);
		expect((init.body as FormData).get("file")).toBe(file);
		expect(out.slug).toBe("demo");
	});

	it("previews with an organization_id form field when given", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ slug: "demo" }),
		});
		const file = new File(["zip-bytes"], "demo.zip", {
			type: "application/zip",
		});

		await previewInstall(file, { organizationId: "org-7" });

		const body = mockAuthFetch.mock.calls[0][1].body as FormData;
		expect(body.get("organization_id")).toBe("org-7");
	});

	it("installs a solution with file, organization_id, and config_values", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ id: "sol-2", slug: "demo" }),
		});
		const file = new File(["zip-bytes"], "demo.zip", {
			type: "application/zip",
		});

		const out = await installSolution({
			file,
			organizationId: "org-9",
			configValues: { api_key: "secret" },
		});

		expect(mockAuthFetch).toHaveBeenCalledTimes(1);
		const [url, init] = mockAuthFetch.mock.calls[0];
		expect(url).toBe("/api/solutions/install");
		expect(init.method).toBe("POST");
		const body = init.body as FormData;
		expect(body.get("file")).toBe(file);
		expect(body.get("organization_id")).toBe("org-9");
		expect(body.get("config_values")).toBe(
			JSON.stringify({ api_key: "secret" }),
		);
		expect(out.id).toBe("sol-2");
	});

	it("installs with ?force=true when force is set", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ id: "sol-4" }),
		});
		const file = new File(["zip-bytes"], "demo.zip", {
			type: "application/zip",
		});

		await installSolution({ file, force: true });

		const [url] = mockAuthFetch.mock.calls[0];
		expect(url).toBe("/api/solutions/install?force=true");
	});

	it("installs globally with empty organization_id when none given", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			json: () => Promise.resolve({ id: "sol-3" }),
		});
		const file = new File(["zip-bytes"], "demo.zip", {
			type: "application/zip",
		});

		await installSolution({ file });

		const body = mockAuthFetch.mock.calls[0][1].body as FormData;
		expect(body.get("organization_id")).toBe("");
		expect(body.get("config_values")).toBe("{}");
	});

	it("throws the server detail on a failed upload", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: false,
			statusText: "Bad Request",
			json: () => Promise.resolve({ detail: "invalid zip" }),
		});
		const file = new File(["x"], "demo.zip");

		await expect(previewInstall(file)).rejects.toThrow(/invalid zip/);
	});
});

describe("exportSolution", () => {
	it("downloads the export zip and parses the server filename", async () => {
		const blob = new Blob(["zipbytes"], { type: "application/zip" });
		mockAuthFetch.mockResolvedValue({
			ok: true,
			headers: new Headers({
				"Content-Disposition": 'attachment; filename="rtm-portal-0.9.0.zip"',
			}),
			blob: () => Promise.resolve(blob),
		});

		const { exportSolution } = await import("./solutions");
		const out = await exportSolution("sol-1");

		// POST, mode in query, body present (empty for shareable) — password
		// must never appear in the URL.
		expect(mockAuthFetch).toHaveBeenCalledWith(
			"/api/solutions/sol-1/export?mode=shareable",
			expect.objectContaining({ method: "POST", body: "{}" }),
		);
		expect(out.filename).toBe("rtm-portal-0.9.0.zip");
		expect(out.blob).toBe(blob);
	});

	it("sends the full-backup password in the body, not the URL", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			headers: new Headers(),
			blob: () => Promise.resolve(new Blob([])),
		});

		const { exportSolution } = await import("./solutions");
		await exportSolution("sol-1", "full", "hunter2", true);

		const [url, options] = mockAuthFetch.mock.calls[0];
		expect(url).toBe("/api/solutions/sol-1/export?mode=full&include_data=true");
		expect(url).not.toContain("hunter2");
		expect(options.method).toBe("POST");
		expect(JSON.parse(options.body)).toEqual({ password: "hunter2" });
	});

	it("falls back to a generic filename without a disposition header", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: true,
			headers: new Headers(),
			blob: () => Promise.resolve(new Blob([])),
		});

		const { exportSolution } = await import("./solutions");
		const out = await exportSolution("sol-1");

		expect(out.filename).toBe("solution-sol-1.zip");
	});

	it("surfaces the server detail on failure", async () => {
		mockAuthFetch.mockResolvedValue({
			ok: false,
			headers: new Headers(),
			json: () => Promise.resolve({ detail: "No stored bundle for this install" }),
		});

		const { exportSolution } = await import("./solutions");
		await expect(exportSolution("sol-1")).rejects.toThrow(
			"No stored bundle for this install",
		);
	});
});
