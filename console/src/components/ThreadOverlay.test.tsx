import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ThreadOverlay, threadPath } from "./ThreadOverlay";

function rect(x: number, y: number, width: number, height: number): DOMRect {
  return {
    x,
    y,
    width,
    height,
    left: x,
    top: y,
    right: x + width,
    bottom: y + height,
    toJSON: () => ({}),
  } as DOMRect;
}

describe("ThreadOverlay", () => {
  it("draws nothing until both ends exist", () => {
    const { container } = render(<ThreadOverlay from={rect(0, 0, 10, 10)} to={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("joins the facing edges with a cubic curve", () => {
    // A value in the right pane (from) back to its bytes in the left pane (to).
    expect(threadPath(rect(600, 100, 80, 20), rect(100, 300, 60, 20))).toBe(
      "M 600 110 C 380 110, 380 310, 160 310",
    );
  });

  it("animates with the thread-draw class (disabled by reduced motion in CSS)", () => {
    const { container } = render(<ThreadOverlay from={rect(0, 0, 10, 10)} to={rect(100, 0, 10, 10)} />);
    const path = container.querySelector("path");
    expect(path?.getAttribute("class")).toBe("thread-draw");
    expect(container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });
});
