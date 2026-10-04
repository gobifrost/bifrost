import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BifrostHeader } from "../../../src/lib/app-sdk/bifrost-header";
import { BifrostProvider } from "../../../src/lib/app-sdk/provider";

const account: typeof fetch = async () =>
	new Response(JSON.stringify({ name: "SDK Viewer" }), { status: 200 });

createRoot(document.getElementById("root")!).render(
	<StrictMode>
		<BifrostProvider
			baseUrl={window.location.origin}
			token="fixture"
			fetchImpl={account}
			supportsTheme
		>
			<BifrostHeader
				title="Docs"
				logo={null}
				action={<button type="button">Organization</button>}
			/>
		</BifrostProvider>
	</StrictMode>,
);
