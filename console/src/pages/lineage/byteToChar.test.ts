import { describe, expect, it } from "vitest";
import { byteSpanToChar } from "./byteToChar";

const encoder = new TextEncoder();

function byteSpanOf(text: string, sub: string): [number, number] {
  const index = text.indexOf(sub);
  const start = encoder.encode(text.slice(0, index)).length;
  return [start, start + encoder.encode(sub).length];
}

describe("B6 byteToChar utility", () => {
  it.each([
    ["ASCII", "user=a.sharma FAILED login", "a.sharma"],
    ["Devanagari (multi-byte)", "तापमान=42 स्थिति=ठीक", "42"],
    ["Devanagari value", "स्थिति=ठीक", "ठीक"],
    ["Emoji surrogate pair", "🔒 auth=success from 103.21.4.77", "103.21.4.77"],
    ["CRLF line breaks", "head\r\nline2=val\r\n", "val"],
  ])("correctly maps %s", (_kind, text, sub) => {
    const span = byteSpanOf(text, sub);
    const [startChar, endChar] = byteSpanToChar(text, span);
    expect(text.slice(startChar, endChar)).toBe(sub);
  });
});
