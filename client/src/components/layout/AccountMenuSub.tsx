import { useRef, type ReactNode } from "react";
import {
	DropdownMenuPortal,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu";

export function AccountMenuSub({
	label,
	icon,
	children,
}: {
	label: string;
	icon: ReactNode;
	children: ReactNode;
}) {
	const content = useRef<HTMLDivElement>(null);
	return (
		<DropdownMenuSub>
			<DropdownMenuSubTrigger
				className="min-h-11"
				onPointerLeave={(event) => {
					// A direct pointer move into a left-opening submenu must not refocus
					// the parent before Radix has updated its pointer direction.
					if (
						event.relatedTarget instanceof Node &&
						content.current?.contains(event.relatedTarget)
					)
						event.preventDefault();
				}}
			>
				{icon}
				{label}
			</DropdownMenuSubTrigger>
			<DropdownMenuPortal>
				<DropdownMenuSubContent
					ref={content}
					collisionPadding={16}
					className="max-w-[calc(100vw-2rem)] max-h-[var(--radix-dropdown-menu-content-available-height)] overflow-y-auto"
				>
					{children}
				</DropdownMenuSubContent>
			</DropdownMenuPortal>
		</DropdownMenuSub>
	);
}
