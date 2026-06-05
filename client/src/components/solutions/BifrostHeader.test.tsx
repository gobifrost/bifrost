import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BifrostProvider } from "@/lib/app-sdk/provider";

import { BifrostHeader } from "./BifrostHeader";

function wrap(ui: React.ReactNode, onLogout?: () => void) {
  return render(
    <BifrostProvider
      baseUrl="https://dev.example"
      token="t"
      onLogout={onLogout}
    >
      {ui}
    </BifrostProvider>,
  );
}

describe("BifrostHeader", () => {
  it("renders the app title", () => {
    wrap(<BifrostHeader title="My Dashboard" />);
    expect(screen.getByText("My Dashboard")).toBeInTheDocument();
  });

  it("calls the provider logout when the logout action is clicked", () => {
    const onLogout = vi.fn();
    wrap(<BifrostHeader title="X" />, onLogout);
    fireEvent.click(screen.getByRole("button", { name: /log ?out/i }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("renders a back-to-Bifrost link pointing at the platform root", () => {
    wrap(<BifrostHeader title="X" />);
    const link = screen.getByRole("link", { name: /bifrost/i });
    expect(link).toHaveAttribute("href", "https://dev.example/");
  });
});
