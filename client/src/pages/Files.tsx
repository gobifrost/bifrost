import { FolderOpen } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { FilesExplorer } from "@/components/files/FilesExplorer";

export function Files() {
	const [searchParams] = useSearchParams();
	const install = searchParams.get("install") ?? undefined;

	return (
		<div className="flex h-full min-h-0 flex-col gap-4">
			<header className="flex shrink-0 items-center gap-2">
				<FolderOpen className="h-5 w-5 text-muted-foreground" />
				<div className="min-w-0">
					<h1 className="text-2xl font-semibold tracking-tight">Files</h1>
					<p className="text-sm text-muted-foreground">
						Browse shares, manage file policies, and test effective access.
					</p>
				</div>
			</header>
			<div className="min-h-0 flex-1">
				<FilesExplorer install={install} />
			</div>
		</div>
	);
}
