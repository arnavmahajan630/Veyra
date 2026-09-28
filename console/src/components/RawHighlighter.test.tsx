import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { RawHighlighter, VIRTUALIZE_AFTER_LINES, type RawHighlighterHandle } from "./RawHighlighter";

const TEXT = '<134>Sep 26 14:05:11 fw01 app[233]: {"msg":"user=a.sharma FAILED login from 103.21.4.77"}';
const user = { id: "user.name", startChar: TEXT.indexOf("a.sharma"), endChar: TEXT.indexOf("a.sharma") + 8, tone: "pinned" as const };
const ip = { id: "src_endpoint.ip", startChar: TEXT.indexOf("103"), endChar: TEXT.indexOf("103") + 11, tone: "muted" as const };

describe("RawHighlighter", () => {
  it("renders the exact text and marks each span", () => {
    const { container } = render(<RawHighlighter text={TEXT} spans={[ip, user]} activeId="src_endpoint.ip" />);
    expect(container.querySelector("pre")?.textContent).toBe(TEXT);
    const marks = container.querySelectorAll("mark");
    expect([...marks].map((m) => [m.dataset.spanId, m.textContent, m.dataset.tone])).toEqual([
      ["user.name", "a.sharma", "pinned"],
      ["src_endpoint.ip", "103.21.4.77", "active"],
    ]);
  });

  it("reports hover and leave", async () => {
    const onSpanHover = vi.fn();
    const { container } = render(<RawHighlighter text={TEXT} spans={[user]} onSpanHover={onSpanHover} />);
    await userEvent.hover(container.querySelector("mark") as HTMLElement);
    expect(onSpanHover).toHaveBeenLastCalledWith("user.name");
    await userEvent.unhover(container.querySelector("pre") as HTMLElement);
    expect(onSpanHover).toHaveBeenLastCalledWith(null);
  });

  it("exposes span rectangles for the thread", () => {
    const ref = createRef<RawHighlighterHandle>();
    render(<RawHighlighter ref={ref} text={TEXT} spans={[user]} />);
    expect(ref.current?.getSpanRect("user.name")).not.toBeNull();
    expect(ref.current?.getSpanRect("nope")).toBeNull();
  });

  it("keeps whitespace and newlines", () => {
    const text = "line one\n  indented\ttab";
    const { container } = render(<RawHighlighter text={text} spans={[]} />);
    expect(container.querySelector("pre")?.textContent).toBe(text);
  });

  it("renders long text in content-visibility blocks without losing a byte", () => {
    const text = Array.from({ length: VIRTUALIZE_AFTER_LINES + 50 }, (_, i) => `line ${i}`).join("\n");
    const { container } = render(<RawHighlighter text={text} spans={[]} />);
    const blocks = container.querySelectorAll<HTMLElement>("[data-block]");
    expect(blocks.length).toBe(6);
    expect(blocks[0]?.style.contentVisibility).toBe("auto");
    expect(container.querySelector("pre")?.textContent).toBe(text);
  });
});
