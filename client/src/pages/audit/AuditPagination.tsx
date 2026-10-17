import { PaginationFooter } from "@/components/pagination/PaginationFooter";

export function AuditPagination({
	count,
	kind = "event",
	page,
	hasNext,
	pending,
	onPrevious,
	onNext,
}: {
	count: number;
	kind?: "event" | "summary";
	page: number;
	hasNext: boolean;
	pending: boolean;
	onPrevious: () => void;
	onNext: () => void;
}) {
	return (
		<PaginationFooter
			aria-label="Audit pagination"
			summary={`${count} ${kind === "summary" ? (count === 1 ? "summary" : "summaries") : `event${count === 1 ? "" : "s"}`} on this page · Page ${page + 1}`}
			pending={pending}
			previousDisabled={page === 0 || pending}
			nextDisabled={!hasNext || pending}
			onPrevious={onPrevious}
			onNext={onNext}
		/>
	);
}
