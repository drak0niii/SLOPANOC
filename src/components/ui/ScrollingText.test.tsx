import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScrollingText } from "./ScrollingText";

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

/** jsdom never computes real layout — `scrollWidth`/`clientWidth` are
 * always 0. Overflow is simulated by defining them directly on the
 * rendered nodes before the hover fires, mirroring how the component
 * itself measures at hover time (not on mount). */
function makeOverflow(outer: Element, inner: Element, distance: number) {
  Object.defineProperty(outer, "clientWidth", { value: 100, configurable: true });
  Object.defineProperty(inner, "scrollWidth", { value: 100 + distance, configurable: true });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  act(() => {
    vi.runOnlyPendingTimers();
  });
  vi.useRealTimers();
});

describe("ScrollingText — delayed hover activation (POST-B7 Item 2)", () => {
  it("1. does not start scrolling before 1000ms", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    makeOverflow(outer, inner, 200);

    fireEvent.mouseEnter(outer);
    advance(999);

    expect(inner.className).not.toContain("anim-scroll-text");
  });

  it("2. starts scrolling once 1000ms elapses", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    makeOverflow(outer, inner, 200);

    fireEvent.mouseEnter(outer);
    advance(1000);

    // The inner <span> remounts (its `key` changes) when the animation
    // starts — re-query rather than trust the pre-remount reference.
    expect(outer.firstElementChild?.className).toContain("anim-scroll-text");
  });

  it("3/4. only the hovered row animates — a second row stays stationary", () => {
    const { container } = render(
      <>
        <ScrollingText>First overflowing chat title that needs scrolling</ScrollingText>
        <ScrollingText>Second overflowing chat title that needs scrolling</ScrollingText>
      </>,
    );
    const spans = container.querySelectorAll("span");
    const outerA = spans[0];
    const outerB = spans[2];
    makeOverflow(outerA, outerA.firstElementChild as HTMLElement, 200);
    makeOverflow(outerB, outerB.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outerA);
    advance(1000);

    expect(outerA.firstElementChild?.className).toContain("anim-scroll-text");
    expect(outerB.firstElementChild?.className).not.toContain("anim-scroll-text");
  });

  it("5. switching rows resets the first and starts an independent 1000ms delay for the second", () => {
    const { container } = render(
      <>
        <ScrollingText>First overflowing chat title that needs scrolling</ScrollingText>
        <ScrollingText>Second overflowing chat title that needs scrolling</ScrollingText>
      </>,
    );
    const spans = container.querySelectorAll("span");
    const outerA = spans[0];
    const outerB = spans[2];
    makeOverflow(outerA, outerA.firstElementChild as HTMLElement, 200);
    makeOverflow(outerB, outerB.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outerA);
    advance(1000);
    expect(outerA.firstElementChild?.className).toContain("anim-scroll-text");

    fireEvent.mouseLeave(outerA);
    fireEvent.mouseEnter(outerB);

    // A's own reset is immediate.
    expect(container.querySelectorAll("span")[0].firstElementChild?.className).not.toContain(
      "anim-scroll-text",
    );
    // B has its OWN fresh 1000ms delay — not yet started.
    advance(999);
    expect(container.querySelectorAll("span")[2].firstElementChild?.className).not.toContain(
      "anim-scroll-text",
    );
    advance(1);
    expect(container.querySelectorAll("span")[2].firstElementChild?.className).toContain(
      "anim-scroll-text",
    );
  });

  it("6. leaving before 1000ms never starts the animation", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    makeOverflow(outer, inner, 200);

    fireEvent.mouseEnter(outer);
    advance(500);
    fireEvent.mouseLeave(outer);
    advance(1000);

    expect(inner.className).not.toContain("anim-scroll-text");
  });

  it("7. leaving after activation stops and resets the animation immediately", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    makeOverflow(outer, outer.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outer);
    advance(1000);
    expect(outer.firstElementChild?.className).toContain("anim-scroll-text");

    fireEvent.mouseLeave(outer);
    expect(outer.firstElementChild?.className).not.toContain("anim-scroll-text");
  });

  it("8. a short, non-overflowing title never scrolls, even after the delay", () => {
    const { container } = render(<ScrollingText>Short</ScrollingText>);
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    // No overflow configured — distance would be 0.

    fireEvent.mouseEnter(outer);
    advance(1000);

    expect(inner.className).not.toContain("anim-scroll-text");
  });

  it("9. repeated enter/leave cycles never leak timers (no animation fires after the last leave)", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    makeOverflow(outer, inner, 200);

    for (let i = 0; i < 5; i++) {
      fireEvent.mouseEnter(outer);
      advance(300);
      fireEvent.mouseLeave(outer);
    }
    advance(1000);

    expect(inner.className).not.toContain("anim-scroll-text");
  });

  it("10. unmounting while a timer is pending does not throw and never fires afterward", () => {
    const { container, unmount } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    const inner = outer.firstElementChild as HTMLElement;
    makeOverflow(outer, inner, 200);

    fireEvent.mouseEnter(outer);
    expect(() => unmount()).not.toThrow();
    expect(() => advance(2000)).not.toThrow();
  });

  it("POST-B7 corrective pass — never sets a native title attribute, at rest", () => {
    const { container } = render(<ScrollingText>Full untruncated title text</ScrollingText>);
    expect(screen.queryByTitle("Full untruncated title text")).not.toBeInTheDocument();
    expect(container.querySelector("[title]")).toBeNull();
  });

  it("POST-B7 corrective pass — never sets a native title attribute while hovering, before activation", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    makeOverflow(outer, outer.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outer);
    advance(500);

    expect(container.querySelector("[title]")).toBeNull();
  });

  it("POST-B7 corrective pass — never sets a native title attribute once the scroll animation is active", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    makeOverflow(outer, outer.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outer);
    advance(1000);
    expect(outer.firstElementChild?.className).toContain("anim-scroll-text");

    expect(container.querySelector("[title]")).toBeNull();
  });

  it("POST-B7 corrective pass — renders no tooltip/popover role of any kind on hover", () => {
    const { container } = render(
      <ScrollingText>A very long overflowing chat title that needs scrolling</ScrollingText>,
    );
    const outer = container.querySelector("span") as HTMLElement;
    makeOverflow(outer, outer.firstElementChild as HTMLElement, 200);

    fireEvent.mouseEnter(outer);
    advance(1000);

    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(document.body.querySelector('[role="tooltip"]')).toBeNull();
    expect(document.body.querySelector('[data-radix-popper-content-wrapper]')).toBeNull();
  });
});
