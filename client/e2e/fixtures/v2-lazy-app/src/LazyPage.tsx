import { sharedLabel } from "./main";

export default function LazyPage() {
	return <h1 data-testid="lazy-page">{sharedLabel}</h1>;
}
