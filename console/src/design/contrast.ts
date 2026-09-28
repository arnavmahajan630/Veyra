// WCAG 2.x relative luminance and contrast ratio, plus the token pairs the console
// actually draws. Tier colours are only ever swatches and bars (never text), so they
// need the 3:1 non-text ratio; everything that is read needs 4.5:1.

export type Rgb = readonly [number, number, number];

export const AA_TEXT = 4.5;
export const AA_UI = 3;

export interface Pair {
  fg: string;
  bg: string;
  min: number;
  use: string;
}

export const PAIRS: readonly Pair[] = [
  { fg: "ink", bg: "paper", min: AA_TEXT, use: "body text" },
  { fg: "ink-2", bg: "paper", min: AA_TEXT, use: "secondary text" },
  { fg: "thread", bg: "paper", min: AA_TEXT, use: "links and primary actions" },
  { fg: "paper", bg: "thread", min: AA_TEXT, use: "text on primary buttons" },
  { fg: "ink", bg: "highlight", min: AA_TEXT, use: "raw bytes under a highlight" },
  { fg: "tier1", bg: "paper", min: AA_UI, use: "tier 1 swatches and bars" },
  { fg: "tier2", bg: "paper", min: AA_UI, use: "tier 2 swatches and bars" },
  { fg: "tier3", bg: "paper", min: AA_UI, use: "tier 3 swatches and bars" },
  { fg: "tier4", bg: "paper", min: AA_UI, use: "tier 4 swatches and bars" },
];

export function hexToRgb(hex: string): Rgb {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match?.[1]) throw new Error(`not a #rrggbb colour: ${hex}`);
  const value = Number.parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export function readColorTokens(css: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  for (const match of css.matchAll(/--color-([\w-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    const [, name, value] = match;
    if (name && value) tokens[name] = value.toLowerCase();
  }
  return tokens;
}
