import { useQuery } from "@tanstack/react-query";
import { HardDrive, Lock, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { listShares } from "@/services/fileStructure";
import { InlineLoader } from "./InlineLoader";

export function SharesOverview({
	scope,
	solution,
	readOnly,
	onSelect,
}: {
	scope: string;
	solution?: string;
	readOnly: boolean;
	onSelect: (location: string, prefix: string) => void;
}) {
	const shares = useQuery({
		queryKey: ["file-shares", scope, solution],
		queryFn: () =>
			solution ? listShares(scope, solution) : listShares(scope),
		retry: false,
	});
	return (
		<section
			aria-label="Browse shares"
			className="flex min-h-0 flex-col overflow-hidden"
		>
			<div className="flex min-w-0 shrink-0 items-end justify-between gap-3 border-b border-border px-4 py-3">
				<div className="min-w-0">
					<h2 className="text-base font-semibold">Shares</h2>
					<p className="mt-1 text-sm text-muted-foreground">
						Choose a root location to browse its folders and files.
					</p>
				</div>
				{shares.isSuccess && shares.data.length > 0 && (
					<p className="shrink-0 text-xs text-muted-foreground">
						{shares.data.length}{" "}
						{shares.data.length === 1 ? "share" : "shares"}
					</p>
				)}
			</div>
			{shares.isPending && (
				<InlineLoader className="px-4 py-3" label="Loading shares…" />
			)}
			{shares.isError && (
				<div role="alert" className="space-y-3 px-4 py-3">
					<p className="text-sm">Shares could not be loaded.</p>
					<Button
						variant="outline"
						disabled={shares.isFetching}
						onClick={() => void shares.refetch()}
					>
						Retry shares
					</Button>
				</div>
			)}
			{shares.isSuccess && shares.data.length === 0 && (
				<div className="flex items-start gap-3 px-4 py-6">
					<span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] bg-primary/10 text-primary">
						<HardDrive className="size-4" />
					</span>
					<div className="min-w-0">
						<h3 className="font-medium">No shares in this scope</h3>
						<p className="mt-1 text-sm text-muted-foreground">
							{readOnly
								? "This solution has no file shares."
								: "Create a share to start organizing files, or choose another organization."}
						</p>
					</div>
				</div>
			)}
			<ul className="min-h-0 overflow-auto divide-y divide-border">
				{shares.data?.map((share) => (
					<li key={share.location}>
						<button
							type="button"
							onClick={() => onSelect(share.location, "")}
							className="group flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none"
						>
							<span className="flex size-9 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] bg-primary/10 text-primary">
								<HardDrive className="size-4" />
							</span>
							<span className="min-w-0 flex-1">
								<span className="block truncate font-semibold text-foreground">
									{share.location}
								</span>
								<span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
									{readOnly || share.readOnly ? (
										<span className="inline-flex items-center gap-1">
											<Lock className="size-3.5" />
											Read only
										</span>
									) : (
										"File share"
									)}
									{share.hasPolicy && (
										<span className="inline-flex items-center gap-1">
											<ShieldCheck className="size-3.5" />
											Policy configured
										</span>
									)}
								</span>
							</span>
						</button>
					</li>
				))}
			</ul>
		</section>
	);
}
