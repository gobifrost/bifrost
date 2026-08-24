import { Building2, ChevronsUpDown, Globe2, Network } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
import type { AuthorizationTarget } from "@/services/authorizationTargets";

function TargetIcon({ target }: { target: AuthorizationTarget }) {
	if (target.kind === "platform") return <Globe2 aria-hidden="true" />;
	if (target.kind === "managed_organizations") {
		return <Network aria-hidden="true" />;
	}
	return <Building2 aria-hidden="true" />;
}

function targetDescription(target: AuthorizationTarget): string {
	if (target.kind === "platform") return "Platform-wide resources";
	if (target.kind === "managed_organizations") {
		return "Browse and support assigned customers";
	}
	return target.is_provider
		? "Your MSP organization"
		: "Customer organization";
}

export function AuthorizationBoundaryPicker() {
	const { targets, selectedTarget, selectedBoundary, setSelectedBoundary } =
		useAuthorizationBoundary();

	if (targets.length < 2 || !selectedTarget || !selectedBoundary) return null;

	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					className="h-9 w-auto min-w-0 max-w-[min(18rem,45vw)] justify-between gap-2 px-3"
					aria-label={`Working in ${selectedTarget.label}`}
				>
					<TargetIcon target={selectedTarget} />
					<span className="min-w-0 text-left">
						<span className="block truncate font-medium">
							{selectedTarget.label}
						</span>
						<span className="block text-[11px] text-muted-foreground sm:text-xs">
							Working in
						</span>
					</span>
					<ChevronsUpDown className="shrink-0 text-muted-foreground" />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent
				align="start"
				className="w-[min(22rem,calc(100vw-2rem))]"
			>
				<DropdownMenuLabel>
					<p className="font-medium text-foreground">Working in</p>
					<p className="mt-0.5 font-normal">
						Choose where your next action applies.
					</p>
				</DropdownMenuLabel>
				<DropdownMenuSeparator />
				<DropdownMenuRadioGroup
					value={selectedBoundary}
					onValueChange={setSelectedBoundary}
				>
					{targets.map((target) => (
						<DropdownMenuRadioItem
							key={target.boundary}
							value={target.boundary}
							className="items-start py-2"
						>
							<TargetIcon target={target} />
							<span className="min-w-0">
								<span className="block truncate font-medium">
									{target.label}
								</span>
								<span className="block text-xs text-muted-foreground">
									{targetDescription(target)}
								</span>
							</span>
						</DropdownMenuRadioItem>
					))}
				</DropdownMenuRadioGroup>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
