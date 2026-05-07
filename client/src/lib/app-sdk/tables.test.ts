import { afterEach, describe, expect, it, vi } from "vitest";
import {
  TableAccessDeniedError,
  TableNotFoundError,
  setDefaultAppScope,
  tables,
} from "./tables";

afterEach(() => {
  // Always reset module-level state so tests don't bleed defaultScope.
  setDefaultAppScope(null);
});

describe("tables web SDK", () => {
  it("get throws TableAccessDeniedError on 403", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response("policy denied", { status: 403 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(tables.get("t1", "row-1")).rejects.toBeInstanceOf(
      TableAccessDeniedError,
    );
  });

  it("get returns null on 404 (row-level URL — could be missing row OR table)", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 404 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.get("t1", "row-1");
    expect(result).toBeNull();
  });

  it("insert posts to /api/tables/{name}/documents", async () => {
    const body = { id: "row-1", data: { k: "v" } };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.insert("t1", { k: "v" });
    expect(result).toEqual(body);
    const url = fetchMock.mock.calls[0][0];
    expect(url).toMatch(/\/api\/tables\/t1\/documents$/);
  });

  it("update PATCHes /api/tables/{name}/documents/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "row-1", data: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.update("t1", "row-1", { k: "v2" });
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.method).toBe("PATCH");
  });

  it("query POSTs to /api/tables/{name}/documents/query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ documents: [], total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.query("t1", { where: { x: { eq: 1 } } });
    const url = fetchMock.mock.calls[0][0];
    expect(url).toMatch(/\/api\/tables\/t1\/documents\/query$/);
  });

  it("delete returns true on 204", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    expect(await tables.delete("t1", "row-1")).toBe(true);
  });

  it("count returns the count", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ count: 42 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    expect(await tables.count("t1")).toBe(42);
  });

  it("upsert POSTs with id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "row-1", data: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.upsert("t1", { id: "row-1", data: { k: "v" } });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.id).toBe("row-1");
    expect(body.upsert).toBe(true);
  });

  it("insert with array posts to /documents/batch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ documents: [{ id: "1", data: {} }] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.insert("t1", [{ data: { x: 1 } }]);
    expect(Array.isArray(result)).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/documents\/batch$/);
  });

  it("delete with array posts to /documents/batch-delete", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ deleted: 2 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.delete("t1", ["a", "b"]);
    expect((result as { deleted: number }).deleted).toBe(2);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/documents\/batch-delete$/);
  });

  it("upsert with array posts to /documents/batch with upsert flag", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          documents: [
            { id: "a", data: {} },
            { id: "b", data: {} },
          ],
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.upsert("t1", [
      { id: "a", data: { k: 1 } },
      { id: "b", data: { k: 2 } },
    ]);
    expect(Array.isArray(result)).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/documents\/batch$/);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.upsert).toBe(true);
    expect(body.documents).toHaveLength(2);
  });

  describe("scope", () => {
    function okJson(body: unknown) {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    it("get appends ?scope=<value> when scope provided, omits otherwise", async () => {
      // mockImplementation returns a fresh Response per call — Response
      // bodies can only be consumed once.
      const fetchMock = vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(okJson({ id: "row-1", data: {} })),
        );
      vi.stubGlobal("fetch", fetchMock);

      await tables.get("t1", "row-1");
      expect(fetchMock.mock.calls[0][0]).not.toContain("scope=");

      await tables.get("t1", "row-1", "org-a");
      expect(fetchMock.mock.calls[1][0]).toContain("?scope=org-a");
    });

    it("insert (single) appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "row-1", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.insert("t1", { k: "v" }, "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/api\/tables\/t1\/documents\?scope=org-a$/,
      );
    });

    it("insert (batch) appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ documents: [] }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.insert("t1", [{ data: { x: 1 } }], "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/batch\?scope=org-a$/,
      );
    });

    it("upsert (single) appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "a", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.upsert("t1", { id: "a", data: { k: 1 } }, "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/api\/tables\/t1\/documents\?scope=org-a$/,
      );
    });

    it("upsert (batch) appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ documents: [] }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.upsert("t1", [{ id: "a", data: { k: 1 } }], "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/batch\?scope=org-a$/,
      );
    });

    it("update appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "row-1", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.update("t1", "row-1", { k: "v2" }, "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/row-1\?scope=org-a$/,
      );
    });

    it("delete (single) appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(new Response(null, { status: 204 }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.delete("t1", "row-1", "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/row-1\?scope=org-a$/,
      );
    });

    it("delete (batch) appends ?scope=<value>", async () => {
      const fetchMock = vi.fn().mockResolvedValue(okJson({ deleted: 1 }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.delete("t1", ["a"], "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/batch-delete\?scope=org-a$/,
      );
    });

    it("query appends ?scope=<value>", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(
          okJson({
            documents: [],
            table_id: "tbl-uuid",
            total: 0,
            limit: 50,
            offset: 0,
          }),
        );
      vi.stubGlobal("fetch", fetchMock);

      await tables.query("t1", { where: { x: { eq: 1 } } }, "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/query\?scope=org-a$/,
      );
    });

    it("count appends ?scope=<value>", async () => {
      const fetchMock = vi.fn().mockResolvedValue(okJson({ count: 7 }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.count("t1", "org-a");
      expect(fetchMock.mock.calls[0][0]).toMatch(
        /\/documents\/count\?scope=org-a$/,
      );
    });

    it("URL-encodes scope values with special characters", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "row-1", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.get("t1", "row-1", "abc/def");
      expect(fetchMock.mock.calls[0][0]).toContain("?scope=abc%2Fdef");
    });

    it("omits scope query param entirely when scope is undefined", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ count: 0 }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.count("t1");
      const url = fetchMock.mock.calls[0][0] as string;
      expect(url).not.toContain("?");
      expect(url).not.toContain("scope=");
    });

    it("omits scope query param when scope is the empty string", async () => {
      // `withScope` treats empty string as "no scope" — falsy. This matches
      // the Python SDK behavior of `scope: str | None = None`.
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ count: 0 }));
      vi.stubGlobal("fetch", fetchMock);

      await tables.count("t1", "");
      const url = fetchMock.mock.calls[0][0] as string;
      expect(url).not.toContain("scope=");
    });
  });

  describe("error model", () => {
    it("query throws TableNotFoundError on 404 (table-level URL)", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(new Response("no such table", { status: 404 })),
      );
      await expect(tables.query("missing")).rejects.toBeInstanceOf(
        TableNotFoundError,
      );
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(new Response("no such table", { status: 404 })),
      );
      await expect(tables.query("missing")).rejects.toThrow("no such table");
    });

    it("count throws TableNotFoundError on 404 (no longer silently 0)", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
      );
      await expect(tables.count("missing")).rejects.toBeInstanceOf(
        TableNotFoundError,
      );
    });

    it("insert(single) throws TableNotFoundError on 404", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
      );
      await expect(
        tables.insert("missing", { x: 1 }),
      ).rejects.toBeInstanceOf(TableNotFoundError);
    });

    it("delete(batch) throws TableNotFoundError on 404", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
      );
      await expect(
        tables.delete("missing", ["a", "b"]),
      ).rejects.toBeInstanceOf(TableNotFoundError);
    });

    it("query throws TableAccessDeniedError on 403 with body", async () => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue(new Response("policy denied", { status: 403 })),
      );
      await expect(tables.query("notes")).rejects.toBeInstanceOf(
        TableAccessDeniedError,
      );
    });

    it("update returns null on 404 (row-level URL — could be missing row OR table)", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
      );
      const result = await tables.update("t1", "row-1", { x: 1 });
      expect(result).toBeNull();
    });

    it("delete(single) returns false on 404 (idempotent)", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
      );
      const result = await tables.delete("t1", "row-1");
      expect(result).toBe(false);
    });

    it("error classes are distinguishable", () => {
      const denied = new TableAccessDeniedError();
      const missing = new TableNotFoundError();
      expect(denied).toBeInstanceOf(TableAccessDeniedError);
      expect(denied).not.toBeInstanceOf(TableNotFoundError);
      expect(missing).toBeInstanceOf(TableNotFoundError);
      expect(missing).not.toBeInstanceOf(TableAccessDeniedError);
      expect(denied.name).toBe("TableAccessDeniedError");
      expect(missing.name).toBe("TableNotFoundError");
    });
  });

  describe("default app scope", () => {
    function okJson(body: unknown) {
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }

    it("auto-applies the configured app scope when caller omits scope", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "row-1", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      setDefaultAppScope("app-org-uuid");
      await tables.get("notes", "row-1");

      const url = fetchMock.mock.calls[0][0] as string;
      expect(url).toContain("?scope=app-org-uuid");
    });

    it("explicit caller scope wins over the default", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ id: "row-1", data: {} }));
      vi.stubGlobal("fetch", fetchMock);

      setDefaultAppScope("app-org-uuid");
      await tables.get("notes", "row-1", "explicit-other-org");

      const url = fetchMock.mock.calls[0][0] as string;
      expect(url).toContain("?scope=explicit-other-org");
      expect(url).not.toContain("app-org-uuid");
    });

    it("setDefaultAppScope returns a cleanup that restores the prior value", async () => {
      const fetchMock = vi
        .fn()
        .mockImplementation(() => Promise.resolve(okJson({ count: 0 })));
      vi.stubGlobal("fetch", fetchMock);

      setDefaultAppScope("outer-org");
      const restoreInner = setDefaultAppScope("inner-org");

      await tables.count("t1");
      expect((fetchMock.mock.calls[0][0] as string)).toContain(
        "scope=inner-org",
      );

      restoreInner();
      await tables.count("t1");
      expect((fetchMock.mock.calls[1][0] as string)).toContain(
        "scope=outer-org",
      );
    });

    it("global app (default scope null) emits no scope param", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(okJson({ count: 0 }));
      vi.stubGlobal("fetch", fetchMock);

      setDefaultAppScope(null);
      await tables.count("t1");

      const url = fetchMock.mock.calls[0][0] as string;
      expect(url).not.toContain("scope=");
    });
  });
});
