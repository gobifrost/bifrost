import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { Tabs as TabsPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function Tabs({
	className,
	orientation = "horizontal",
	...props
}: React.ComponentProps<typeof TabsPrimitive.Root>) {
	return (
		<TabsPrimitive.Root
			data-slot="tabs"
			orientation={orientation}
			data-orientation={orientation}
			className={cn(
				"group/tabs flex gap-2 data-horizontal:flex-col",
				className,
			)}
			{...props}
		/>
	);
}

const tabsListVariants = cva(
	"group/tabs-list inline-flex w-fit items-center justify-center rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted p-1 text-muted-foreground shadow-none group-data-horizontal/tabs:h-10 group-data-horizontal/tabs:overflow-y-hidden group-data-horizontal/tabs:data-[variant=line]:h-auto group-data-horizontal/tabs:data-[variant=line]:min-h-10 group-data-horizontal/tabs:[&.flex-wrap]:h-auto [&.flex-wrap]:gap-1 [&.flex-wrap>[data-slot=tabs-trigger]]:min-h-11 group-data-vertical/tabs:h-fit group-data-vertical/tabs:flex-col group-data-vertical/tabs:p-1 data-[variant=line]:rounded-none data-[variant=line]:border-0 group-data-horizontal/tabs:data-[variant=line]:border-b data-[variant=line]:bg-transparent data-[variant=line]:p-0",
	{
		variants: {
			variant: {
				default: "",
				line: "gap-1",
			},
		},
		defaultVariants: {
			variant: "default",
		},
	},
);

function TabsList({
	className,
	variant = "default",
	...props
}: React.ComponentProps<typeof TabsPrimitive.List> &
	VariantProps<typeof tabsListVariants>) {
	return (
		<TabsPrimitive.List
			data-slot="tabs-list"
			data-variant={variant}
			className={cn(tabsListVariants({ variant }), className)}
			{...props}
		/>
	);
}

function TabsTrigger({
	className,
	...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
	return (
		<TabsPrimitive.Trigger
			data-slot="tabs-trigger"
			className={cn(
				"relative inline-flex h-[calc(100%-1px)] flex-1 items-center justify-center gap-1.5 rounded-[var(--bf-radius-control)] border border-transparent px-2 py-1 text-sm font-medium whitespace-nowrap text-foreground/60 transition-[color,background-color,border-color,box-shadow,transform] duration-[var(--bf-motion-feedback)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 dark:text-muted-foreground dark:hover:text-foreground group-data-vertical/tabs:w-full group-data-vertical/tabs:justify-start group-data-vertical/tabs:px-3 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
				"group-data-[variant=line]/tabs-list:h-auto group-data-[variant=line]/tabs-list:self-stretch group-data-[variant=line]/tabs-list:bg-transparent group-data-[variant=line]/tabs-list:data-active:bg-transparent dark:group-data-[variant=line]/tabs-list:data-active:border-transparent dark:group-data-[variant=line]/tabs-list:data-active:bg-transparent",
				"data-active:bg-background data-active:text-primary dark:data-active:border-border dark:data-active:bg-border/40 dark:data-active:text-primary",
				"after:absolute after:bg-primary after:opacity-0 after:transition-opacity after:duration-[var(--bf-motion-feedback)] group-data-horizontal/tabs:after:inset-x-0 group-data-horizontal/tabs:after:bottom-0 group-data-horizontal/tabs:after:h-0.5 group-data-vertical/tabs:after:inset-y-0 group-data-vertical/tabs:after:-right-1 group-data-vertical/tabs:after:w-0.5 group-data-[variant=line]/tabs-list:data-active:after:opacity-100 motion-reduce:transition-none motion-reduce:after:transition-none",
				className,
			)}
			{...props}
		/>
	);
}

function TabsContent({
	className,
	...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
	return (
		<TabsPrimitive.Content
			data-slot="tabs-content"
			className={cn("flex-1 text-sm outline-none", className)}
			{...props}
		/>
	);
}

export { Tabs, TabsList, TabsTrigger, TabsContent, tabsListVariants };
