/**
 * Stable public error constructors shared by the installable binding and the
 * hosted SDK factory. The factory receives these constructors as peers so an
 * error it throws has the same identity an App imported from `bifrost`.
 */
export class TableAccessDeniedError extends Error {
	constructor(message = "Access denied") {
		super(message);
		this.name = "TableAccessDeniedError";
	}
}

export class TableNotFoundError extends Error {
	constructor(message = "Table not found") {
		super(message);
		this.name = "TableNotFoundError";
	}
}

export class FileAccessDeniedError extends Error {
	constructor(message = "Access denied") {
		super(message);
		this.name = "FileAccessDeniedError";
	}
}

export class FileNotFoundError extends Error {
	constructor(message = "File not found") {
		super(message);
		this.name = "FileNotFoundError";
	}
}

export class FilePolicyError extends Error {
	constructor(message = "File policy error") {
		super(message);
		this.name = "FilePolicyError";
	}

}
