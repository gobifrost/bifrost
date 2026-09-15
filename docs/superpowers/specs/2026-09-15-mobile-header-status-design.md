# Mobile Header Status Design

## Problem

On phone-sized viewports, the compact workspace-status strip can render below the primary header toolbar. When only file activity is present, the watcher icon occupies a second row and makes the header appear broken despite the primary controls fitting on one line.

## Design

Keep the primary mobile toolbar unchanged and hide the secondary workspace-status strip below the existing `sm` breakpoint. Continue showing the strip at tablet widths through the compact-header breakpoint, and keep the full inline indicators on wide screens. The watcher feature and its desktop/tablet behavior remain intact.

Alternatives considered were removing the watcher everywhere or moving it into another menu. Both add unnecessary product change when responsive prioritization solves the reported problem.

## Verification

Update the existing `Header` component test to prove that phone-sized rendering omits the secondary status strip while retaining navigation and account controls, and that the strip remains present immediately above the mobile breakpoint.
