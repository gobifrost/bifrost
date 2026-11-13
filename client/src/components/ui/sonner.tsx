import { cn } from "@/lib/utils";
import { Toaster as Sonner, ToasterProps } from "sonner";
import { useTheme } from "@/contexts/ThemeContext";

const Toaster = ({ toastOptions, ...props }: ToasterProps) => {
	const { theme } = useTheme();

	return (
		<Sonner
			theme={(theme as ToasterProps["theme"]) || "system"}
			className="group"
			toastOptions={{
				...toastOptions,
				classNames: {
					...toastOptions?.classNames,
					description: cn(
						"text-muted-foreground! text-sm! leading-5!",
						toastOptions?.classNames?.description,
					),
					title: cn(
						"text-sm! leading-5!",
						toastOptions?.classNames?.title,
					),
					toast: cn(
						"rounded-[var(--bf-radius-surface)]! p-4! pr-14! [overflow-wrap:anywhere]",
						toastOptions?.classNames?.toast,
					),
					closeButton: cn(
						"size-11! right-1! left-auto! top-1! transform-none! border-0! bg-transparent!",
						toastOptions?.classNames?.closeButton,
					),
				},
			}}
			closeButton
			style={
				{
					zIndex: 45,
					"--normal-bg": "var(--popover)",
					"--normal-text": "var(--popover-foreground)",
					"--normal-border": "var(--border)",
				} as React.CSSProperties
			}
			{...props}
		/>
	);
};

export { Toaster };
