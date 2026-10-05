import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
	BifrostProvider,
	useBifrostContext,
	useBranding,
	useOrganizations,
} from "bifrost";

function App() {
	const branding = useBranding();
	const organizations = useOrganizations();
	const { toggleTheme } = useBifrostContext();
	if (branding.loading || organizations.loading) return <p>Loading</p>;
	if (branding.error || organizations.error)
		return (
			<p role="alert">{String(branding.error || organizations.error)}</p>
		);
	return (
		<main>
			<h1>SDK platform hooks</h1>
			{branding.rectangleLogoUrl && (
				<img alt="Platform logo" src={branding.rectangleLogoUrl} />
			)}
			<label>
				Organization
				<select>
					{organizations.data?.map((org) => (
						<option key={org.id} value={org.id}>
							{org.name}
						</option>
					))}
				</select>
			</label>
			<button
				style={{
					backgroundColor: "var(--primary)",
					color: "var(--primary-foreground)",
				}}
				onClick={toggleTheme}
			>
				Toggle theme
			</button>
			<output aria-label="Palette color">
				{branding.colors.primary}
			</output>
		</main>
	);
}

createRoot(document.getElementById("root")!).render(
	<StrictMode>
		<BifrostProvider
			baseUrl={window.location.origin}
			token=""
			theme="light"
			supportsTheme
		>
			<App />
		</BifrostProvider>
	</StrictMode>,
);
