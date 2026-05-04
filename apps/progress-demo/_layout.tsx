import { Outlet } from "react-router-dom";
export default function Layout() {
  return (
    <div style={{ minHeight: "100vh", background: "#0f172a", color: "#e2e8f0" }}>
      <Outlet />
    </div>
  );
}
