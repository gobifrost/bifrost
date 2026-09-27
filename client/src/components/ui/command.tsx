"use client";

import * as React from "react";
import { Command as CommandPrimitive } from "cmdk";

import { cn } from "@/lib/utils";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { InputGroup, InputGroupAddon } from "@/components/ui/input-group";
import { SearchIcon, CheckIcon } from "lucide-react";

function filterCommandItems(
	value: string,
	search: string,
	keywords: string[] = [],
) {
	const terms = search.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
	const searchable = [value, ...keywords].join(" ").toLocaleLowerCase();
	return terms.every((term) => searchable.includes(term)) ? 1 : 0;
}

function scrollWithinCommandList(item: HTMLElement) {
	const list = item.closest<HTMLElement>('[cmdk-list=""]');
	if (!list) return;

	const listRect = list.getBoundingClientRect();
	const itemRect = item.getBoundingClientRect();
	const styles = window.getComputedStyle(list);
	const scrollPaddingTop = Number.parseFloat(styles.scrollPaddingTop) || 0;
	const scrollPaddingBottom =
		Number.parseFloat(styles.scrollPaddingBottom) || 0;
	const visibleTop = listRect.top + scrollPaddingTop;
	const visibleBottom = listRect.bottom - scrollPaddingBottom;

	if (itemRect.top < visibleTop) {
		list.scrollTop -= visibleTop - itemRect.top;
	} else if (itemRect.bottom > visibleBottom) {
		list.scrollTop += itemRect.bottom - visibleBottom;
	}
}

function setCommandItemRef(
	ref: React.Ref<HTMLDivElement> | undefined,
	item: HTMLDivElement | null,
) {
	if (item) {
		// cmdk calls scrollIntoView when its selected item changes. The native
		// method may scroll every ancestor, including a cross-origin iframe's host
		// page. Keep that internal selection bookkeeping inside the command list.
		Object.defineProperty(item, "scrollIntoView", {
			configurable: true,
			value: () => scrollWithinCommandList(item),
		});
	}

	if (typeof ref === "function") {
		ref(item);
	} else if (ref) {
		ref.current = item;
	}
}

function Command({
	className,
	filter = filterCommandItems,
	...props
}: React.ComponentProps<typeof CommandPrimitive>) {
	return (
		<CommandPrimitive
			data-slot="command"
			className={cn(
				"flex size-full flex-col overflow-hidden rounded-[var(--bf-radius-surface)] bg-popover p-1 text-popover-foreground",
				className,
			)}
			filter={filter}
			{...props}
		/>
	);
}

function CommandDialog({
	title = "Command Palette",
	description = "Search for a command to run...",
	children,
	className,
	showCloseButton = true,
	commandProps,
	...props
}: React.ComponentProps<typeof Dialog> & {
	title?: string;
	description?: string;
	className?: string;
	showCloseButton?: boolean;
	commandProps?: React.ComponentProps<typeof Command>;
}) {
	return (
		<Dialog {...props}>
			<DialogContent
				className={cn(
					"top-1/3 translate-y-0 overflow-hidden rounded-[var(--bf-radius-feature)]! p-0",
					className,
				)}
				showCloseButton={showCloseButton}
			>
				{/* sr-only header must live INSIDE DialogContent so it only exists
            in the a11y tree while the dialog is open. */}
				<DialogHeader className="sr-only">
					<DialogTitle>{title}</DialogTitle>
					<DialogDescription>{description}</DialogDescription>
				</DialogHeader>
				{/* Auto-wrap children in <Command> — this is a published contract:
            already-built v2 app bundles (via lib/bifrost-runtime.ts) pass
            CommandInput/CommandList directly as children and rely on this
            wrapper for the cmdk context. Do not remove. */}
				<Command {...commandProps}>{children}</Command>
			</DialogContent>
		</Dialog>
	);
}

function CommandInput({
	className,
	onValueChange,
	ref,
	...props
}: React.ComponentProps<typeof CommandPrimitive.Input>) {
	const inputRef = React.useRef<HTMLInputElement>(null);
	const resetFrameRef = React.useRef<number | null>(null);
	const commandInputRef = React.useCallback(
		(input: HTMLInputElement | null) => {
			inputRef.current = input;
			if (typeof ref === "function") {
				ref(input);
			} else if (ref) {
				ref.current = input;
			}
		},
		[ref],
	);
	const handleValueChange = React.useCallback(
		(search: string) => {
			onValueChange?.(search);
			if (resetFrameRef.current !== null) {
				cancelAnimationFrame(resetFrameRef.current);
			}
			resetFrameRef.current = requestAnimationFrame(() => {
				const commandRoot = inputRef.current?.closest('[cmdk-root=""]');
				const list = commandRoot?.querySelector<HTMLElement>('[cmdk-list=""]');
				if (list) list.scrollTop = 0;
				resetFrameRef.current = null;
			});
		},
		[onValueChange],
	);

	React.useEffect(
		() => () => {
			if (resetFrameRef.current !== null) {
				cancelAnimationFrame(resetFrameRef.current);
			}
		},
		[],
	);

	return (
		<div data-slot="command-input-wrapper" className="p-1 pb-0">
			<InputGroup className="h-11! rounded-[var(--bf-radius-control)] bg-background lg:h-9!">
				<CommandPrimitive.Input
					ref={commandInputRef}
					data-slot="command-input"
					className={cn(
						"w-full text-sm outline-hidden disabled:cursor-not-allowed disabled:opacity-50",
						className,
					)}
					onValueChange={handleValueChange}
					{...props}
				/>
				<InputGroupAddon>
					<SearchIcon className="size-4 shrink-0 opacity-50" />
				</InputGroupAddon>
			</InputGroup>
		</div>
	);
}

function CommandList({
	className,
	...props
}: React.ComponentProps<typeof CommandPrimitive.List>) {
	const listRef = React.useRef<HTMLDivElement>(null);

	React.useEffect(() => {
		const target = listRef.current;
		if (!target) return;

		const handleWheel = (e: WheelEvent) => {
			const { scrollHeight, clientHeight } = target;
			const isScrollable = scrollHeight > clientHeight;

			if (isScrollable) {
				// Manually scroll the content
				target.scrollTop += e.deltaY;

				// Prevent the event from scrolling parent elements
				const isAtTop = target.scrollTop === 0 && e.deltaY < 0;
				const isAtBottom =
					target.scrollTop + clientHeight >= scrollHeight &&
					e.deltaY > 0;

				if (!isAtTop && !isAtBottom) {
					e.preventDefault();
					e.stopPropagation();
				}
			}
		};

		// Use native event listener with passive: false to allow preventDefault
		target.addEventListener("wheel", handleWheel, { passive: false });

		return () => {
			target.removeEventListener("wheel", handleWheel);
		};
	}, []);

	return (
		<CommandPrimitive.List
			ref={listRef}
			data-slot="command-list"
			className={cn(
				"max-h-72 scroll-py-1 overflow-x-hidden overflow-y-auto outline-none",
				className,
			)}
			{...props}
		/>
	);
}

function CommandEmpty({
	className,
	...props
}: React.ComponentProps<typeof CommandPrimitive.Empty>) {
	return (
		<CommandPrimitive.Empty
			data-slot="command-empty"
			className={cn("py-6 text-center text-sm", className)}
			{...props}
		/>
	);
}

function CommandGroup({
	className,
	...props
}: React.ComponentProps<typeof CommandPrimitive.Group>) {
	return (
		<CommandPrimitive.Group
			data-slot="command-group"
			className={cn(
				"overflow-hidden p-1 text-foreground **:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:py-1.5 **:[[cmdk-group-heading]]:text-xs **:[[cmdk-group-heading]]:font-medium **:[[cmdk-group-heading]]:text-muted-foreground",
				className,
			)}
			{...props}
		/>
	);
}

function CommandSeparator({
	className,
	...props
}: React.ComponentProps<typeof CommandPrimitive.Separator>) {
	return (
		<CommandPrimitive.Separator
			data-slot="command-separator"
			className={cn("my-1 h-px bg-border/50", className)}
			{...props}
		/>
	);
}

function CommandItem({
	className,
	children,
	ref,
	...props
}: React.ComponentProps<typeof CommandPrimitive.Item>) {
	const commandItemRef = React.useCallback(
		(item: HTMLDivElement | null) => setCommandItemRef(ref, item),
		[ref],
	);

	return (
		<CommandPrimitive.Item
			ref={commandItemRef}
			data-slot="command-item"
			className={cn(
				"group/command-item relative flex min-h-11 lg:min-h-9 cursor-default items-center gap-2 rounded-[var(--bf-radius-control)] px-2 py-1.5 text-sm outline-hidden select-none transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none data-[disabled=true]:pointer-events-none data-[disabled=true]:opacity-50 data-selected:bg-muted data-selected:text-foreground [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4 data-selected:*:[svg]:text-foreground",
				className,
			)}
			{...props}
		>
			{children}
			<CheckIcon className="ml-auto opacity-0 group-has-data-[slot=command-shortcut]/command-item:hidden group-data-[checked=true]/command-item:opacity-100" />
		</CommandPrimitive.Item>
	);
}

function CommandShortcut({
	className,
	...props
}: React.ComponentProps<"span">) {
	return (
		<span
			data-slot="command-shortcut"
			className={cn(
				"ml-auto text-xs tracking-widest text-muted-foreground group-data-selected/command-item:text-foreground",
				className,
			)}
			{...props}
		/>
	);
}

export {
	Command,
	CommandDialog,
	CommandInput,
	CommandList,
	CommandEmpty,
	CommandGroup,
	CommandItem,
	CommandShortcut,
	CommandSeparator,
};
