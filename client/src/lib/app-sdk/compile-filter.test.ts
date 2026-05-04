import { describe, expect, it } from "vitest";
import { compileFilterToExpr } from "./use-table";

describe("compileFilterToExpr", () => {
  describe("shorthand equality", () => {
    it("string", () => {
      expect(compileFilterToExpr({ status: "active" })).toEqual({
        eq: [{ row: "status" }, "active"],
      });
    });

    it("boolean", () => {
      expect(compileFilterToExpr({ pinned: true })).toEqual({
        eq: [{ row: "pinned" }, true],
      });
    });

    it("number", () => {
      expect(compileFilterToExpr({ count: 5 })).toEqual({
        eq: [{ row: "count" }, 5],
      });
    });

    it("null shorthand routes through is_null (Expr validator rejects null in eq)", () => {
      // The policy validator forbids null literals in eq/neq because NULL
      // semantics differ between evaluator and SQL pushdown. We compile
      // `{ assignee: null }` (and `{ assignee: { eq: null } }`) to an
      // is_null check instead.
      expect(compileFilterToExpr({ assignee: null })).toEqual({
        is_null: { row: "assignee" },
      });
      expect(compileFilterToExpr({ assignee: { eq: null } })).toEqual({
        is_null: { row: "assignee" },
      });
      expect(compileFilterToExpr({ assignee: { neq: null } })).toEqual({
        not: { is_null: { row: "assignee" } },
      });
    });
  });

  describe("operator forms", () => {
    it("eq operator (explicit)", () => {
      expect(compileFilterToExpr({ status: { eq: "active" } })).toEqual({
        eq: [{ row: "status" }, "active"],
      });
    });

    it("neq", () => {
      expect(compileFilterToExpr({ status: { neq: "closed" } })).toEqual({
        neq: [{ row: "status" }, "closed"],
      });
    });

    it("ne (alias for neq)", () => {
      expect(compileFilterToExpr({ status: { ne: "closed" } })).toEqual({
        neq: [{ row: "status" }, "closed"],
      });
    });

    it("lt/lte/gt/gte", () => {
      expect(compileFilterToExpr({ amount: { gt: 100 } })).toEqual({
        gt: [{ row: "amount" }, 100],
      });
      expect(compileFilterToExpr({ amount: { gte: 100 } })).toEqual({
        gte: [{ row: "amount" }, 100],
      });
      expect(compileFilterToExpr({ amount: { lt: 1000 } })).toEqual({
        lt: [{ row: "amount" }, 1000],
      });
      expect(compileFilterToExpr({ amount: { lte: 1000 } })).toEqual({
        lte: [{ row: "amount" }, 1000],
      });
    });

    it("in", () => {
      expect(
        compileFilterToExpr({ category: { in: ["a", "b"] } }),
      ).toEqual({
        in: [{ row: "category" }, ["a", "b"]],
      });
    });

    it("in rejects empty arrays (Expr validator requires non-empty)", () => {
      expect(() =>
        compileFilterToExpr({ category: { in: [] } }),
      ).toThrow(/non-empty/);
    });

    it("is_null: true", () => {
      expect(compileFilterToExpr({ deleted_at: { is_null: true } })).toEqual({
        is_null: { row: "deleted_at" },
      });
    });

    it("is_null: false (compiles to NOT is_null)", () => {
      expect(compileFilterToExpr({ deleted_at: { is_null: false } })).toEqual({
        not: { is_null: { row: "deleted_at" } },
      });
    });
  });

  describe("multi-field and multi-operator", () => {
    it("two fields combined as AND", () => {
      expect(
        compileFilterToExpr({ status: "active", pinned: true }),
      ).toEqual({
        and: [
          { eq: [{ row: "status" }, "active"] },
          { eq: [{ row: "pinned" }, true] },
        ],
      });
    });

    it("two operators on same field combined as AND", () => {
      expect(
        compileFilterToExpr({ amount: { gte: 100, lt: 1000 } }),
      ).toEqual({
        and: [
          { gte: [{ row: "amount" }, 100] },
          { lt: [{ row: "amount" }, 1000] },
        ],
      });
    });

    it("mixed field + operator forms", () => {
      expect(
        compileFilterToExpr({
          client_id: "abc",
          amount: { gte: 100 },
        }),
      ).toEqual({
        and: [
          { eq: [{ row: "client_id" }, "abc"] },
          { gte: [{ row: "amount" }, 100] },
        ],
      });
    });

    it("empty filter compiles to null (subscribe with no filter)", () => {
      // Server's Expr validator rejects {and: []} (logic ops require ≥2
      // operands). Returning null lets callers pass it through to
      // `tables.subscribe(tableId, null, ...)` for the unfiltered case.
      expect(compileFilterToExpr({})).toBeNull();
    });
  });

  describe("operators not supported by policy Expr AST", () => {
    it.each(["contains", "starts_with", "ends_with", "has_key"])(
      "%s throws with a clear message",
      (op) => {
        expect(() =>
          compileFilterToExpr({
            name: { [op]: "x" } as unknown as Record<string, unknown>,
          }),
        ).toThrow(new RegExp(op));
      },
    );

    it("unknown operator throws", () => {
      expect(() =>
        compileFilterToExpr({
          name: { fizzbuzz: "x" } as unknown as Record<string, unknown>,
        }),
      ).toThrow(/fizzbuzz/);
    });
  });

  describe("drift guard with server _build_document_filters", () => {
    // Operators the server accepts in dict-shorthand `where` (per
    // api/src/routers/tables.py::_build_document_filters). Update both ends
    // when adding new ones.
    const SERVER_OPERATORS = [
      "eq",
      "ne",
      "neq",
      "contains",
      "starts_with",
      "ends_with",
      "gt",
      "gte",
      "lt",
      "lte",
      "in",
      "is_null",
      "has_key",
    ];

    it("compileFilterToExpr handles every server-side operator (compile or throw — never silently drop)", () => {
      for (const op of SERVER_OPERATORS) {
        // Either it compiles, or it throws a clear error. The unacceptable
        // outcome is silent acceptance + nothing on the wire.
        let threw = false;
        let compiled: unknown = undefined;
        try {
          compiled = compileFilterToExpr({
            x: { [op]: op === "in" ? [1] : 1 } as unknown as Record<
              string,
              unknown
            >,
          });
        } catch (e) {
          threw = true;
          // The thrown message must mention the operator name so a future
          // dev hits a clear breadcrumb.
          expect(String((e as Error).message)).toContain(op);
        }
        expect(threw || compiled !== undefined).toBe(true);
      }
    });
  });
});
