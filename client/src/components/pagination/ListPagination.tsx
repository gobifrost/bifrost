import { Loader2 } from "lucide-react";

import {
	Pagination,
	PaginationContent,
	PaginationItem,
	PaginationNext,
	PaginationPrevious,
} from "@/components/ui/pagination";

interface ListPaginationProps {
	offset: number;
	limit: number;
	total: number;
	isFetching?: boolean;
	onPageChange: (offset: number) => void;
}

export function ListPagination({
	offset,
	limit,
	total,
	isFetching = false,
	onPageChange,
}: ListPaginationProps) {
	const page = Math.floor(offset / limit);
	const totalPages = Math.max(1, Math.ceil(total / limit));
	const previousDisabled = page === 0 || isFetching;
	const nextDisabled = page + 1 >= totalPages || isFetching;
	const first = total === 0 ? 0 : offset + 1;
	const last = Math.min(offset + limit, total);

	return (
		<div className="flex flex-wrap items-center justify-between gap-3 border-t px-2 py-3 text-sm text-muted-foreground">
			<span aria-live="polite">
				{first}–{last} of {total}
			</span>
			<Pagination className="mx-0 w-auto justify-end">
				<PaginationContent>
					<PaginationItem>
						<PaginationPrevious
							href="#"
							onClick={(event) => {
								event.preventDefault();
								if (!previousDisabled) {
									onPageChange(Math.max(0, offset - limit));
								}
							}}
							className={
								previousDisabled
									? "pointer-events-none opacity-50"
									: "cursor-pointer"
							}
							aria-disabled={previousDisabled}
						/>
					</PaginationItem>
					<li className="flex min-w-28 items-center justify-center gap-1.5 px-2 tabular-nums">
						{isFetching ? (
							<Loader2
								className="h-3.5 w-3.5 animate-spin"
								aria-label="Loading page"
							/>
						) : null}
						Page {page + 1} of {totalPages}
					</li>
					<PaginationItem>
						<PaginationNext
							href="#"
							onClick={(event) => {
								event.preventDefault();
								if (!nextDisabled) onPageChange(offset + limit);
							}}
							className={
								nextDisabled
									? "pointer-events-none opacity-50"
									: "cursor-pointer"
							}
							aria-disabled={nextDisabled}
						/>
					</PaginationItem>
				</PaginationContent>
			</Pagination>
		</div>
	);
}
