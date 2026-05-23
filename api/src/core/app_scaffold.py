"""Canonical source templates for newly created Bifrost apps."""

APP_LAYOUT_SOURCE = '''import { Outlet } from "bifrost";

export default function RootLayout() {
  return (
    <div className="min-h-screen bg-background">
      <Outlet />
    </div>
  );
}
'''

APP_INDEX_SOURCE = '''export default function HomePage() {
  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Welcome</h1>
      <p className="text-muted-foreground">
        Start building your app by editing this page or adding new files.
      </p>
    </div>
  );
}
'''
