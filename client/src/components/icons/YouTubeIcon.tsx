import type { SVGProps } from "react";

export function YouTubeIcon(props: SVGProps<SVGSVGElement>) {
	return (
		<svg
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth="2"
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
			{...props}
		>
			<rect x="2" y="5" width="20" height="14" rx="4" />
			<path d="m10 9 5 3-5 3Z" />
		</svg>
	);
}
