import {
	existsSync,
	mkdirSync,
	readFileSync,
	renameSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { join } from "node:path";

// The launcher owns initialization. Playwright imports the capture spec again
// when replacing a failed worker, so module loading must never clear results.
export function initializeDocsCapture(args, docsRepo) {
	const docs = args.some(
		(arg, index) =>
			arg === "--project=docs" ||
			(arg === "--project" && args[index + 1] === "docs"),
	);
	if (!docs) return;
	if (!docsRepo)
		throw new Error("DOCS_REPO_PATH is required for docs capture");
	const directory = join(docsRepo, ".tmp-captures");
	mkdirSync(directory, { recursive: true });
	rmSync(join(directory, "results.json"), { force: true });
}

export function writeCaptureResult(docsRepo, result) {
	const directory = join(docsRepo, ".tmp-captures");
	mkdirSync(directory, { recursive: true });
	const filename = join(directory, "results.json");
	const previous = existsSync(filename)
		? JSON.parse(readFileSync(filename, "utf8"))
		: [];
	const results = previous.filter((item) => item.id !== result.id);
	results.push(result);
	const temporary = join(directory, `results-${process.pid}.tmp`);
	writeFileSync(temporary, JSON.stringify(results, null, 2));
	renameSync(temporary, filename);
}
