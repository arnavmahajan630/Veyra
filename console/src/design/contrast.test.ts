// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PAIRS, contrastRatio, hexToRgb, readColorTokens } from "./contrast";

const tokens = readColorTokens(readFileSync(new URL("./tokens.css", import.meta.url), "utf8"));

describe("contrast maths", () => {
  it("matches the WCAG reference values", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#777777")).toBe(1);
  });

  it("parses #rrggbb and rejects anything else", () => {
    expect(hexToRgb("#2E3A8C")).toEqual([46, 58, 140]);
    expect(() => hexToRgb("blue")).toThrow("not a #rrggbb colour");
  });
});

describe("design tokens (make console-contrast)", () => {
  it("defines the C5 palette verbatim", () => {
    expect(tokens).toMatchObject({
      paper: "#fbfbf8",
      ink: "#1d2330",
      rule: "#d9dde3",
      thread: "#2e3a8c",
      turmeric: "#c98a0b",
      tier1: "#2f7d5b",
      tier2: "#b7791f",
      tier3: "#6b5ca5",
      tier4: "#a23b3b",
    });
  });

  it.each(PAIRS)("$fg on $bg ($use) reaches $min:1", ({ fg, bg, min }) => {
    const foreground = tokens[fg];
    const background = tokens[bg];
    expect(foreground, `token --color-${fg}`).toBeDefined();
    expect(background, `token --color-${bg}`).toBeDefined();
    expect(contrastRatio(foreground as string, background as string)).toBeGreaterThanOrEqual(min);
  });
});
