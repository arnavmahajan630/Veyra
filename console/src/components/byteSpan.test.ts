import { describe, expect, it } from "vitest";
import { byteSpanToChar } from "./byteSpan";

const encoder = new TextEncoder();

/** The byte span of the first occurrence of `sub` in `text`, as IF-ULPF would record it. */
function byteSpanOf(text: string, sub: string): [number, number] {
  const index = text.indexOf(sub);
  const start = encoder.encode(text.slice(0, index)).length;
  return [start, start + encoder.encode(sub).length];
}

describe("byteSpanToChar", () => {
  it.each([
    ["ASCII", "user=a.sharma FAILED login", "a.sharma"],
    ["Devanagari before the value", "तापमान=42 स्थिति=ठीक", "42"],
    ["Devanagari value", "स्थिति=ठीक", "ठीक"],
    ["emoji (surrogate pair) before the value", "😀 ip=10.2.3.4", "10.2.3.4"],
    ["JSON-escaped quotes", '{"msg":"user=\\"x\\" ok"}', '\\"x\\"'],
    ["CRLF line breaks", "a\r\nb=1", "1"],
  ])("%s", (_name, text, sub) => {
    const [start, end] = byteSpanToChar(text, byteSpanOf(text, sub));
    expect(text.slice(start, end)).toBe(sub);
  });

  it("maps an empty span to an empty range", () => {
    expect(byteSpanToChar("abc", [2, 2])).toEqual([2, 2]);
  });

  it("stops at the end of the text for a span that runs past it", () => {
    expect(byteSpanToChar("abc", [1, 99])).toEqual([1, 3]);
  });
});
