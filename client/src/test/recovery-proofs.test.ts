import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { RecoveryProofPool } from "../../e2e/setup/recovery-proofs";

const directories: string[] = [];
afterEach(async () => {
	await Promise.all(
		directories
			.splice(0)
			.map((d) => rm(d, { recursive: true, force: true })),
	);
});
async function directory() {
	const d = await mkdtemp(join(tmpdir(), "bifrost-proof-test-"));
	directories.push(d);
	return d;
}
const credentials = {
	userId: "person",
	totpSecret: "JBSWY3DPEHPK3PXP",
	recoveryCodes: ["AAAA-BBBB", "CCCC-DDDD"],
	mfaSetupCounter: 0,
};

describe("one-use MFA proof storage", () => {
	it("serializes independent pool instances and persists replenished proofs", async () => {
		const dir = await directory();
		const replenish = vi.fn(async () => ["EEEE-FFFF", "GGGG-HHHH"]);
		const first = new RecoveryProofPool(dir, replenish);
		const second = new RecoveryProofPool(dir, replenish);
		const submitted: string[] = [];
		const submit = async (code: string) => {
			submitted.push(code);
			return "session";
		};
		await Promise.all([
			first.run(credentials, submit),
			second.run(credentials, submit),
		]);
		expect(new Set(submitted)).toEqual(new Set(credentials.recoveryCodes));
		expect(replenish).toHaveBeenCalledTimes(1);
		await new RecoveryProofPool(dir, replenish).run(credentials, submit);
		expect(submitted[2]).toBe("EEEE-FFFF");
	});

	it("does not retry or reuse a proof after a failed submission", async () => {
		const dir = await directory();
		const replenish = vi.fn(async () => ["EEEE-FFFF"]);
		const pool = new RecoveryProofPool(dir, replenish);
		const rejected = vi.fn(async () => {
			throw new Error("rejected");
		});
		await expect(pool.run(credentials, rejected)).rejects.toThrow(
			"rejected",
		);
		expect(rejected).toHaveBeenCalledTimes(1);
		const next = vi.fn(async () => "session");
		await new RecoveryProofPool(dir, replenish).run(credentials, next);
		expect(next).toHaveBeenCalledWith("CCCC-DDDD");
	});
});
