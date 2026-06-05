import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostHeader } from "./bifrost-header";
import { BifrostProvider } from "./provider";

describe("BifrostHeader (SDK, self-contained)", () => {
  it("renders the title + back-to-Bifrost link and logs out via context", () => {
    const onLogout = vi.fn();
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t" onLogout={onLogout}>
        <BifrostHeader title="My Dashboard" />
      </BifrostProvider>,
    );
    expect(screen.getByText("My Dashboard")).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Bifrost/i });
    expect(back.getAttribute("href")).toBe("https://dev.example/");

    screen.getByRole("button", { name: /log out/i }).click();
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("renders an optional action slot", () => {
    render(
      <BifrostProvider baseUrl="https://dev.example" token="t">
        <BifrostHeader title="X" action={<span>extra</span>} />
      </BifrostProvider>,
    );
    expect(screen.getByText("extra")).toBeInTheDocument();
  });
});
