import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AuditRetentionInfo } from "@/services/auditRetention";
import { AuditRetentionBanner } from "./AuditRetentionBanner";
import { formatAuditTime } from "./auditRetentionFormat";

const retention: AuditRetentionInfo = {
	hot_days: 90,
	archive_days: 365,
	oldest_in_database: "2026-07-06T09:30:00Z",
	archived_through: "2026-07-05T23:59:00Z",
	archived_segments: 12,
	archived_rows: 4200,
};

describe("AuditRetentionBanner", () => {
	it("says how far back the database goes and what is archived", async () => {
		const onExport = vi.fn();
		render(
			<AuditRetentionBanner retention={retention} onExport={onExport} />,
		);

		expect(
			screen.getByText(
				`Showing events since ${formatAuditTime("2026-07-06T09:30:00Z")}. Older events are archived through ${formatAuditTime("2026-07-05T23:59:00Z")}.`,
			),
		).toBeInTheDocument();
		await userEvent.click(screen.getByRole("button", { name: "Export" }));
		expect(onExport).toHaveBeenCalledOnce();
	});

	it("leaves out the archive sentence when nothing is archived", () => {
		render(
			<AuditRetentionBanner
				retention={{ ...retention, archived_through: null }}
				onExport={vi.fn()}
			/>,
		);

		expect(
			screen.getByText(
				`Showing events since ${formatAuditTime("2026-07-06T09:30:00Z")}.`,
			),
		).toBeInTheDocument();
		expect(screen.queryByText(/archived through/)).not.toBeInTheDocument();
	});

	it("says when there are no audit events yet", () => {
		render(
			<AuditRetentionBanner
				retention={{
					...retention,
					oldest_in_database: null,
					archived_through: null,
				}}
				onExport={vi.fn()}
			/>,
		);

		expect(screen.getByText("No audit events yet.")).toBeInTheDocument();
	});

	it("renders nothing without retention", () => {
		const { container } = render(
			<AuditRetentionBanner retention={null} onExport={vi.fn()} />,
		);

		expect(container).toBeEmptyDOMElement();
	});
});
