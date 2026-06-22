import { Outlet } from "react-router-dom";

export default function Layout() {
	return (
		<div style={{ minHeight: "100vh", background: "#0b0b0f", color: "#e7e7ea", fontFamily: "ui-sans-serif, system-ui, sans-serif" }}>
			<Outlet />
		</div>
	);
}
