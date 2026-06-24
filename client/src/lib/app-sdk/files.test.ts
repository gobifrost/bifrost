import { afterEach, describe, expect, it, vi } from "vitest";
import {
	FileAccessDeniedError,
	FileNotFoundError,
	FilePolicyError,
	files,
	setBifrostTransport,
} from "./files";

let restoreTransport: (() => void) | null = null;

afterEach(() => {
	restoreTransport?.();
	restoreTransport = null;
	vi.unstubAllGlobals();
});

function okJson(body: unknown) {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { "content-type": "application/json" },
	});
}

function okNoContent() {
	return new Response(null, { status: 204 });
}

describe("files web SDK", () => {
	it("read posts to /api/files/read with workspace defaults", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(okJson({ content: "hello", binary: false }));
		vi.stubGlobal("fetch", fetchMock);

		await expect(files.read("docs/readme.txt")).resolves.toBe("hello");

		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(fetchMock.mock.calls[0][0]).toBe("/api/files/read");
		expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
			path: "docs/readme.txt",
			location: "workspace",
			mode: "cloud",
			binary: false,
			scope: null,
		});
	});

	it("readBytes decodes base64 content from /api/files/read", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(okJson({ content: "AQIDBA==", binary: true }));
		vi.stubGlobal("fetch", fetchMock);

		const result = await files.readBytes("bin/data.dat");

		expect(Array.from(result)).toEqual([1, 2, 3, 4]);
		expect(JSON.parse(fetchMock.mock.calls[0][1].body).binary).toBe(true);
	});

	it("write posts text content to /api/files/write", async () => {
		const fetchMock = vi.fn().mockResolvedValue(okNoContent());
		vi.stubGlobal("fetch", fetchMock);

		await files.write("notes/a.txt", "body", { location: "shared" });

		expect(fetchMock.mock.calls[0][0]).toBe("/api/files/write");
		expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
			path: "notes/a.txt",
			content: "body",
			location: "shared",
			mode: "cloud",
			binary: false,
			scope: null,
		});
	});

	it("writeBytes base64-encodes byte content", async () => {
		const fetchMock = vi.fn().mockResolvedValue(okNoContent());
		vi.stubGlobal("fetch", fetchMock);

		await files.writeBytes("bin/data.dat", new Uint8Array([1, 2, 3]));

		const body = JSON.parse(fetchMock.mock.calls[0][1].body);
		expect(body.content).toBe("AQID");
		expect(body.binary).toBe(true);
	});

	it("delete posts to /api/files/delete", async () => {
		const fetchMock = vi.fn().mockResolvedValue(okNoContent());
		vi.stubGlobal("fetch", fetchMock);

		await files.delete("old.txt");

		expect(fetchMock.mock.calls[0][0]).toBe("/api/files/delete");
		expect(JSON.parse(fetchMock.mock.calls[0][1].body).path).toBe(
			"old.txt",
		);
	});

	it("list returns file names and can request metadata", async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			okJson({
				files: ["a.txt"],
				files_metadata: [
					{
						path: "a.txt",
						etag: "e1",
						last_modified: "2026-01-01T00:00:00Z",
					},
				],
			}),
		);
		vi.stubGlobal("fetch", fetchMock);

		const result = await files.list("", { includeMetadata: true });

		expect(result.files).toEqual(["a.txt"]);
		expect(result.filesMetadata[0]?.etag).toBe("e1");
		expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
			directory: "",
			include_metadata: true,
		});
	});

	it("exists returns the server boolean", async () => {
		const fetchMock = vi.fn().mockResolvedValue(okJson({ exists: true }));
		vi.stubGlobal("fetch", fetchMock);

		await expect(files.exists("a.txt")).resolves.toBe(true);
		expect(fetchMock.mock.calls[0][0]).toBe("/api/files/exists");
	});

	it("signedUrl posts to /api/files/signed-url with workspace defaults", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(okJson({ url: "https://s3/x", path: "_repo/x", expires_in: 600 }));
		vi.stubGlobal("fetch", fetchMock);

		const result = await files.signedUrl("x.txt", { method: "GET" });

		expect(result.url).toBe("https://s3/x");
		expect(fetchMock.mock.calls[0][0]).toBe("/api/files/signed-url");
		expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
			path: "x.txt",
			method: "GET",
			content_type: "application/octet-stream",
			location: "workspace",
			scope: null,
		});
	});

	it("signedUrls uses the batch endpoint without workflow endpoints", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(okJson({
				results: [
					{ path: "a.txt", resolved_path: "_repo/a", method: "GET", url: "https://s3/a", expires_in: 600, status_code: 200 },
					{ path: "b.txt", resolved_path: "_repo/b", method: "GET", url: "https://s3/b", expires_in: 600, status_code: 200 },
				],
			}));
		vi.stubGlobal("fetch", fetchMock);

		const result = await files.signedUrls(["a.txt", "b.txt"], {
			method: "GET",
		});

		expect(result.map((r) => r.url)).toEqual(["https://s3/a", "https://s3/b"]);
		expect(fetchMock.mock.calls.map((call) => String(call[0]))).toEqual([
			"/api/files/signed-urls",
		]);
		expect(JSON.parse(fetchMock.mock.calls[0][1].body).requests).toHaveLength(2);
		expect(fetchMock.mock.calls.map((call) => String(call[0]).includes("workflow"))).toEqual([
			false,
		]);
	});

	it("upload uses a signed PUT URL and does not call workflow endpoints", async () => {
			const fetchMock = vi
				.fn()
				.mockResolvedValueOnce(okJson({ url: "https://s3/upload", path: "_repo/report.pdf", expires_in: 600 }))
				.mockResolvedValueOnce(new Response(null, { status: 200 }))
				.mockResolvedValueOnce(new Response(null, { status: 204 }));
		vi.stubGlobal("fetch", fetchMock);

			const result = await files.upload("report.pdf", "pdf", { contentType: "application/pdf" });

			expect(result.path).toBe("_repo/report.pdf");
			expect(fetchMock.mock.calls[0][0]).toBe("/api/files/signed-url");
			expect(fetchMock.mock.calls[1][0]).toBe("https://s3/upload");
			expect(fetchMock.mock.calls[1][1].method).toBe("PUT");
			expect(await (fetchMock.mock.calls[1][1].body as Blob).text()).toBe("pdf");
			expect(fetchMock.mock.calls[2][0]).toBe("/api/files/complete-upload");
			expect(JSON.parse(fetchMock.mock.calls[2][1].body).content_type).toBe("application/pdf");
			expect(fetchMock.mock.calls.map((call) => String(call[0]).includes("workflow"))).toEqual([
				false,
				false,
				false,
			]);
		});

	it("download uses a signed GET URL and returns a Blob", async () => {
		const blob = new Blob(["hello"], { type: "text/plain" });
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(okJson({ url: "https://s3/download", path: "_repo/a.txt", expires_in: 600 }))
			.mockResolvedValueOnce(new Response(blob, { status: 200 }));
		vi.stubGlobal("fetch", fetchMock);

		const result = await files.download("a.txt");

		expect(await result.text()).toBe("hello");
		expect(fetchMock.mock.calls[1][0]).toBe("https://s3/download");
		expect(fetchMock.mock.calls[1][1].method).toBe("GET");
	});

	it("maps 403, 404, and policy validation errors to distinct classes", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(new Response("denied", { status: 403 })),
		);
		await expect(files.read("a.txt")).rejects.toBeInstanceOf(
			FileAccessDeniedError,
		);

		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(new Response("missing", { status: 404 })),
		);
		await expect(files.read("a.txt")).rejects.toBeInstanceOf(
			FileNotFoundError,
		);

		vi.stubGlobal(
			"fetch",
			vi.fn().mockResolvedValue(new Response("bad policy", { status: 400 })),
		);
		await expect(files.read("a.txt")).rejects.toBeInstanceOf(
			FilePolicyError,
		);
	});

	it("uses provider transport baseUrl and headers when installed", async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValue(okJson({ exists: false }));
		vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("unused")));
		restoreTransport = setBifrostTransport({
			baseUrl: "https://api.example",
			fetchImpl: fetchMock as unknown as typeof fetch,
			headers: {
				Authorization: "Bearer token",
				"X-Bifrost-App": "app-123",
			},
		});

		await files.exists("a.txt");

		expect(fetchMock.mock.calls[0][0]).toBe("https://api.example/api/files/exists");
		expect(fetchMock.mock.calls[0][1].credentials).toBe("omit");
		expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
			"Bearer token",
		);
		expect(fetchMock.mock.calls[0][1].headers["X-Bifrost-App"]).toBe(
			"app-123",
		);
	});
});
