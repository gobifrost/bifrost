import { describe, expect, it } from "vitest";

import {
	FileAccessDeniedError,
	FileNotFoundError,
	FilePolicyError,
	TableAccessDeniedError,
	TableNotFoundError,
} from "./errors";

describe("SDK error constructors", () => {
	it.each([
		[TableAccessDeniedError, "TableAccessDeniedError", "Access denied"],
		[TableNotFoundError, "TableNotFoundError", "Table not found"],
		[FileAccessDeniedError, "FileAccessDeniedError", "Access denied"],
		[FileNotFoundError, "FileNotFoundError", "File not found"],
		[FilePolicyError, "FilePolicyError", "File policy error"],
	])("keeps %s as an Error with its established default message", (ErrorType, name, message) => {
		const error = new ErrorType();
		expect(error).toBeInstanceOf(Error);
		expect(error).toBeInstanceOf(ErrorType);
		expect(error.name).toBe(name);
		expect(error.message).toBe(message);
	});
});
