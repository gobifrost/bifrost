import { useNavigation } from "react-router-dom";

export function RouteTransitionProgress() {
	const navigation = useNavigation();

	if (navigation.state === "idle") {
		return null;
	}

	return (
		<div
			role="progressbar"
			aria-label="Loading page"
			className="route-transition-progress-track pointer-events-none fixed inset-x-0 top-0 z-[100] h-0.5 overflow-hidden"
		>
			<div
				data-state="loading"
				className="route-transition-progress-fill h-full w-full transition-transform duration-200 ease-out"
			/>
		</div>
	);
}
