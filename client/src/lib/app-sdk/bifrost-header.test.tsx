import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostHeader } from "./bifrost-header";
import { BifrostProvider } from "./provider";

describe("BifrostHeader (SDK, self-contained)", () => {
  it("renders the title + back-to-Bifrost link and logs out via the user menu", () => {
    const onLogout = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" onLogout={onLogout}>
        <BifrostHeader title="My Dashboard" />
      </BifrostProvider>,
    );
    expect(screen.getByText("My Dashboard")).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Bifrost/i });
    expect(back.getAttribute("href")).toBe("https://dev.example/");

    // Log out lives inside the user-menu dropdown now — open it first.
    fireEvent.click(screen.getByRole("button", { name: /account menu/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /log out/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("shows the theme toggle ONLY when the app opts in via supportsTheme", () => {
    const { rerender } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t">
        <BifrostHeader title="X" />
      </BifrostProvider>,
    );
    // Default: app did not declare supportsTheme → no toggle.
    expect(screen.queryByRole("button", { name: /theme/i })).toBeNull();

    rerender(
      <BifrostProvider baseUrl="https://dev.example" token="t" supportsTheme>
        <BifrostHeader title="X" />
      </BifrostProvider>,
    );
    expect(screen.getByRole("button", { name: /theme/i })).toBeInTheDocument();
  });

  it("renders an optional action slot", () => {
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t">
        <BifrostHeader title="X" action={<span>extra</span>} />
      </BifrostProvider>,
    );
    expect(screen.getByText("extra")).toBeInTheDocument();
  });

  it("styles itself inline (no dependency on Tailwind/theme CSS variables)", () => {
    // Standalone apps may have no Tailwind build and none of the platform's
    // theme CSS variables. The header must carry its own visual styling so it
    // is not unstyled there. Pin that the chrome comes from inline styles, not
    // semantic Tailwind utility classes that would resolve to nothing.
    const { container } = render(
      <BifrostProvider baseUrl="https://dev.example" token="t">
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
        <BifrostProvider baseUrl="https://dev.example" token="t" supportsTheme theme="dark">
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
        <BifrostProvider baseUrl="https://dev.example" token="t">
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
      <BifrostProvider baseUrl="https://dev.example" token="t">
        <BifrostHeader title="Light one" />
      </BifrostProvider>,
    );
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" supportsTheme theme="dark">
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
      <BifrostProvider baseUrl="https://dev.example" token="t">
        <BifrostHeader title="X" className="my-custom-class" />
      </BifrostProvider>,
    );
    const header = container.querySelector("header");
    expect(header!.className).toContain("my-custom-class");
    // Inline styling is still present (override augments, doesn't replace).
    expect(header!.style.display).toBe("flex");
  });
});
