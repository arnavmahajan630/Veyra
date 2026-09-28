function utf8Length(codePoint: number): number {
  if (codePoint < 0x80) return 1;
  if (codePoint < 0x800) return 2;
  if (codePoint < 0x10000) return 3;
  return 4;
}

/**
 * IF-ULPF field offsets are [start, end) byte spans over the UTF-8 encoding of the
 * decoded raw text; JavaScript strings index UTF-16 code units. Convert one span.
 */
export function byteSpanToChar(text: string, span: readonly [number, number]): [number, number] {
  const [start, end] = span;
  let bytes = 0;
  let index = 0;
  let charStart: number | null = null;
  while (index < text.length && bytes < end) {
    if (charStart === null && bytes >= start) charStart = index;
    const codePoint = text.codePointAt(index) as number;
    bytes += utf8Length(codePoint);
    index += codePoint > 0xffff ? 2 : 1;
  }
  return [charStart ?? index, index];
}
