import type { SVGProps } from "react";

export function MicrosoftGraphIcon({
	className,
	...props
}: SVGProps<SVGSVGElement>) {
	return (
		<svg
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.8"
			strokeLinecap="round"
			strokeLinejoin="round"
			className={className}
			aria-hidden="true"
			{...props}
		>
			<path d="M7.2 7.5 12 4.8l4.8 2.7v5.4L12 15.6l-4.8-2.7Z" />
			<path d="m7.2 12.9-3.7 2.2v4.1L7 21.1l3.8-2.2v-3.8M16.8 12.9l3.7 2.2v4.1L17 21.1l-3.8-2.2v-3.8" />
		</svg>
	);
}
