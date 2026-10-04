import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, NavLink, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BifrostHeader } from "./bifrost-header";
import type { BifrostHeaderNav } from "./bifrost-header";
import { BifrostProvider } from "./provider";

// BifrostHeader fires an authed `GET /api/auth/me` on mount. Without a stub
// fetchImpl the provider's authedFetch hits the REAL global fetch against the
// fake `https://dev.example` base, leaking a live DNS lookup (getaddrinfo
// ENOTFOUND) per render. Those pending lookups pile up on the worker's event
// loop and can starve a co-scheduled test file (a dynamic import() then exceeds
// its timeout) — surfacing as a spurious cross-file failure. Resolve every
// header fetch locally so the suite never touches the network.
const noNetwork: typeof fetch = async () => new Response(null, { status: 404 });

// react-router's NavLink is passed straight through: it must satisfy the
// header's link contract (a type error here means apps can't pass it).
const NAV: BifrostHeaderNav = {
  items: [
    { label: "Home", to: "/", end: true },
    { label: "About", to: "/about" },
  ],
  link: NavLink,
};

// happy-dom evaluates matchMedia against its viewport and fires resize, so the
// header's breakpoint is exercised for real.
function setViewportWidth(width: number) {
  act(() => {
    (window as unknown as { happyDOM: { setViewport(v: { width: number }): void } }).happyDOM.setViewport({
      width,
    });
  });
}

function Location() {
  return <span data-testid="location">{useLocation().pathname}</span>;
}

function renderAt(
  path: string,
  header: ReactNode,
  { fetchImpl = noNetwork, onLogout }: { fetchImpl?: typeof fetch; onLogout?: () => void } = {},
) {
  return render(
    <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={fetchImpl} onLogout={onLogout} supportsTheme>
      <MemoryRouter initialEntries={[path]}>
        {header}
        <Location />
      </MemoryRouter>
    </BifrostProvider>,
  );
}

describe("BifrostHeader (SDK, self-contained)", () => {
  it("renders the title + back-to-Bifrost link and logs out via the user menu", () => {
    const onLogout = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork} onLogout={onLogout}>
        <BifrostHeader title="My Dashboard" />
      </BifrostProvider>,
    );
    expect(screen.getByText("My Dashboard")).toBeInTheDocument();
    // Back link returns to the platform's Apps page (where the user came from),
    // not the bare root.
    const back = screen.getByRole("link", { name: /Bifrost/i });
    expect(back.getAttribute("href")).toBe("https://dev.example/apps");

    // Log out lives inside the user-menu dropdown now — open it first.
    fireEvent.click(screen.getByRole("button", { name: /account menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /log out/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("shows the theme toggle ONLY when the app opts in via supportsTheme", () => {
    const { rerender } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
        <BifrostHeader title="X" />
      </BifrostProvider>,
    );
    // Default: app did not declare supportsTheme → no toggle.
    expect(screen.queryByRole("button", { name: /theme/i })).toBeNull();

    rerender(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork} supportsTheme>
        <BifrostHeader title="X" />
      </BifrostProvider>,
    );
    expect(screen.getByRole("button", { name: /theme/i })).toBeInTheDocument();
  });

  it("renders an optional action slot", () => {
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
        <BifrostHeader title="X" action={<span>extra</span>} />
      </BifrostProvider>,
    );
    expect(screen.getByText("extra")).toBeInTheDocument();
  });

  it("shows expired dev auth instead of the Account fallback", async () => {
    const expiredAuth: typeof fetch = async () =>
      new Response(
        JSON.stringify({
          error: "bifrost_dev_auth_expired",
          detail: "Your CLI token has expired. Restart `bifrost solution start`.",
        }),
        {
          status: 401,
          headers: {
            "Content-Type": "application/json",
            "X-Bifrost-Dev-Auth": "expired",
          },
        },
      );

    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={expiredAuth}>
        <BifrostHeader title="X" />
      </BifrostProvider>,
    );

    expect(await screen.findByText("Session expired")).toBeInTheDocument();
    expect(screen.queryByText("Account")).toBeNull();
  });

  it("keeps one row on wide viewports: the title truncates instead of the bar wrapping", () => {
    const { container } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork} supportsTheme>
        <BifrostHeader title="A very long app title that should not force the app viewport wider" />
      </BifrostProvider>,
    );

    const header = container.querySelector("header")!;
    const title = screen.getByText(/very long app title/i);
    const left = title.parentElement!;
    const bar = left.parentElement!;
    const right = screen.getByRole("button", { name: /account menu/i }).closest("div")!.parentElement!;

    // Without `nav` the header is just the bar: title side + controls side.
    expect(Array.from(header.children)).toEqual([bar]);
    expect(Array.from(bar.children)).toEqual([left, right]);
    expect(bar.style.flexWrap).toBe("nowrap");
    expect(bar.style.alignItems).toBe("center");
    // The title side takes only leftover space and may shrink to nothing...
    expect(left.style.flex).toBe("1 1 0%");
    expect(left.style.minWidth).toBe("0");
    expect(left.style.overflow).toBe("hidden");
    // ...so the title is what gives way, with an ellipsis.
    expect(title.style.whiteSpace).toBe("nowrap");
    expect(title.style.overflow).toBe("hidden");
    expect(title.style.textOverflow).toBe("ellipsis");
    expect(title.style.minWidth).toBe("0");
    expect(right.style.flex).toBe("0 1 auto");
    expect(right.contains(screen.getByRole("button", { name: /theme/i }))).toBe(true);
  });

  it("renders nav as a tab row on wide viewports, marking the active route", () => {
    renderAt("/about", <BifrostHeader title="Tabs" nav={NAV} />);

    const row = screen.getByRole("navigation", { name: "Primary" });
    const home = within(row).getByRole("link", { name: "Home" });
    const about = within(row).getByRole("link", { name: "About" });
    expect(home.getAttribute("href")).toBe("/");
    expect(home.classList.contains("bfh-tab")).toBe(true);
    // The router marks the active link; the header styles that attribute.
    expect(about.getAttribute("aria-current")).toBe("page");
    expect(home.getAttribute("aria-current")).toBeNull();
    expect(document.getElementById("bifrost-header-style-light")!.textContent).toContain(
      '.bfh-tab[aria-current="page"]',
    );
    // Wide viewports keep the inline controls; there is no menu button.
    expect(screen.getByRole("button", { name: /account menu/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
  });

  it("switches layout live and closes the open panel when the viewport widens", () => {
    // Mount wide first: happy-dom's MediaQueryList only reports a change after
    // its first observed flip away from `false`.
    renderAt("/", <BifrostHeader title="Resizing" />);
    try {
      setViewportWidth(390);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      setViewportWidth(1024);
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByRole("button", { name: /account menu/i })).toBeInTheDocument();

      setViewportWidth(390);
      expect(screen.queryByRole("dialog")).toBeNull();
    } finally {
      setViewportWidth(1024);
    }
  });

  describe("below the 640px breakpoint", () => {
    beforeEach(() => setViewportWidth(390));
    afterEach(() => setViewportWidth(1024));

    it("collapses to one row: back link, title and a single menu button", () => {
      const { container } = renderAt("/", <BifrostHeader title="Phone" nav={NAV} action={<button type="button">Export</button>} />);

      const header = container.querySelector("header")!;
      const bar = screen.getByText("Phone").parentElement!.parentElement!;
      expect(Array.from(header.children)).toEqual([bar]);
      const menuButton = screen.getByRole("button", { name: "Open menu" });
      expect(menuButton.getAttribute("aria-expanded")).toBe("false");
      expect(within(bar).getAllByRole("button")).toEqual([menuButton]);
      expect(within(bar).getAllByRole("link").map((a) => a.textContent)).toEqual(["Bifrost"]);
      expect(screen.queryByRole("navigation")).toBeNull();
      expect(screen.queryByText("Export")).toBeNull();
    });

    it("opens a panel with the nav links, action, theme toggle and account section", async () => {
      const onLogout = vi.fn();
      const me: typeof fetch = async () =>
        new Response(JSON.stringify({ name: "Alex Rivera", email: "alex@example.com" }), { status: 200 });
      renderAt(
        "/about",
        <BifrostHeader title="Phone" nav={NAV} action={<button type="button">Export</button>} />,
        { fetchImpl: me, onLogout },
      );

      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      const panel = screen.getByRole("dialog", { name: "Menu" });
      expect(screen.getByRole("button", { name: "Close menu" }).getAttribute("aria-expanded")).toBe("true");
      const links = within(panel).getAllByRole("link");
      expect(links.map((a) => a.textContent)).toEqual(["Home", "About"]);
      expect(links[1].getAttribute("aria-current")).toBe("page");
      expect(within(panel).getByRole("button", { name: "Export" })).toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "Dark mode" })).toBeInTheDocument();
      expect(await within(panel).findByText("Alex Rivera")).toBeInTheDocument();
      expect(within(panel).getByText("alex@example.com")).toBeInTheDocument();
      // Focus moves into the panel.
      expect(document.activeElement).toBe(links[0]);

      fireEvent.click(within(panel).getByRole("button", { name: "Log out" }));
      expect(onLogout).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("keeps Tab inside the panel", () => {
      renderAt("/", <BifrostHeader title="Phone" nav={NAV} />);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      const panel = screen.getByRole("dialog", { name: "Menu" });
      const first = within(panel).getByRole("link", { name: "Home" });
      const last = within(panel).getByRole("button", { name: "Log out" });

      last.focus();
      fireEvent.keyDown(document, { key: "Tab" });
      expect(document.activeElement).toBe(first);
      fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
      expect(document.activeElement).toBe(last);
    });

    it("closes on Escape and returns focus to the menu button", () => {
      renderAt("/", <BifrostHeader title="Phone" nav={NAV} />);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Open menu" }));
    });

    it("closes when a nav link is followed", () => {
      renderAt("/", <BifrostHeader title="Phone" nav={NAV} />);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      fireEvent.click(within(screen.getByRole("dialog")).getByRole("link", { name: "About" }));

      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByTestId("location").textContent).toBe("/about");
    });

    it("closes when the backdrop is tapped", () => {
      const { container } = renderAt("/", <BifrostHeader title="Phone" />);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      fireEvent.click(container.querySelector(".bfh-backdrop")!);
      expect(screen.queryByRole("dialog")).toBeNull();
    });

    it("without nav, the panel still holds the theme toggle and account", () => {
      renderAt("/", <BifrostHeader title="Phone" />);
      fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
      const panel = screen.getByRole("dialog", { name: "Menu" });

      expect(within(panel).queryByRole("navigation")).toBeNull();
      expect(within(panel).getByRole("button", { name: "Dark mode" })).toBeInTheDocument();
      expect(within(panel).getByText("Account")).toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    });
  });

  it("styles itself inline (no dependency on Tailwind/theme CSS variables)", () => {
    // Standalone apps may have no Tailwind build and none of the platform's
    // theme CSS variables. The header must carry its own visual styling so it
    // is not unstyled there. Pin that the chrome comes from inline styles, not
    // semantic Tailwind utility classes that would resolve to nothing.
    const { container } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
        <BifrostHeader title="Styled" />
      </BifrostProvider>,
    );
    const header = container.querySelector("header");
    expect(header).not.toBeNull();
    // Layout + chrome is inline, not class-driven.
    expect(header!.style.display).toBe("flex");
    expect(header!.style.borderBottom).not.toBe("");
    // The header must NOT rely on the platform theme tokens that break standalone.
    expect(header!.className).not.toMatch(/text-muted-foreground|bg-accent|border-b\b/);
    // The hover stylesheet is injected and scoped so it can't leak into the
    // host. The id is theme-suffixed (light/dark sheets coexist) and the
    // selectors are theme-qualified so one theme's sheet can't clobber another's.
    const injected = document.getElementById("bifrost-header-style-light");
    expect(injected).not.toBeNull();
    expect(injected!.textContent).toContain('[data-bifrost-header-theme="light"]');
    expect(injected!.textContent).toContain("[data-bifrost-header]");
  });

  it("recolors its own chrome for dark theme when the app supports theming", () => {
    // A theme-aware app that flips to dark must not be left with a light header
    // bar (the D3 "unstyled/half-themed header" miss). The header keys its own
    // surface color off the context theme.
    localStorage.setItem("theme", "dark");
    try {
      const { container } = render(
        <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork} supportsTheme theme="dark">
          <BifrostHeader title="Dark" />
        </BifrostProvider>,
      );
      const header = container.querySelector("header")!;
      // Light surface is #ffffff; dark must be a dark surface, not white.
      expect(header.style.background.toLowerCase()).not.toBe("rgb(255, 255, 255)");
      expect(header.style.background.toLowerCase()).not.toBe("#ffffff");
    } finally {
      localStorage.removeItem("theme");
    }
  });

  it("stays light-chromed when the app does NOT support theming", () => {
    // An app with hardcoded light colors never opts in; the header stays light
    // regardless of any stray stored theme, so it matches the app it sits above.
    localStorage.setItem("theme", "dark");
    try {
      const { container } = render(
        <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
          <BifrostHeader title="Light" />
        </BifrostProvider>,
      );
      const header = container.querySelector("header")!;
      expect(header.style.background.toLowerCase()).toBe("#ffffff");
    } finally {
      localStorage.removeItem("theme");
    }
  });

  it("a light and a dark header coexist without their hover sheets clobbering", () => {
    // Two headers on one page (one light app, one dark app). Each gets its own
    // theme-suffixed sheet AND theme-qualified selectors, so the last-appended
    // sheet can't set hover colors for the other (Codex finding).
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
        <BifrostHeader title="Light one" />
      </BifrostProvider>,
    );
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork} supportsTheme theme="dark">
        <BifrostHeader title="Dark one" />
      </BifrostProvider>,
    );
    const light = document.getElementById("bifrost-header-style-light");
    const dark = document.getElementById("bifrost-header-style-dark");
    expect(light).not.toBeNull();
    expect(dark).not.toBeNull();
    // Each sheet is qualified to its own theme — neither uses a bare, shared
    // [data-bifrost-header] hover selector that would leak across themes.
    expect(light!.textContent).toContain('[data-bifrost-header-theme="light"]');
    expect(light!.textContent).not.toContain('[data-bifrost-header-theme="dark"]');
    expect(dark!.textContent).toContain('[data-bifrost-header-theme="dark"]');
  });

  it("still allows author className overrides (applied alongside inline styles)", () => {
    const { container } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t" fetchImpl={noNetwork}>
        <BifrostHeader title="X" className="my-custom-class" />
      </BifrostProvider>,
    );
    const header = container.querySelector("header");
    expect(header!.className).toContain("my-custom-class");
    // Inline styling is still present (override augments, doesn't replace).
    expect(header!.style.display).toBe("flex");
  });
});
