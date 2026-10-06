/**
 * A `--bf-motion-*` token's duration in seconds, for framer-motion
 * transitions that must follow the CSS tokens (which drop to 0ms under
 * reduced motion).
 */
export function motionSeconds(token: `--bf-motion-${string}`): number {
	const value = getComputedStyle(document.documentElement).getPropertyValue(
		token,
	);
	return (Number.parseFloat(value) || 0) / 1000;
}
