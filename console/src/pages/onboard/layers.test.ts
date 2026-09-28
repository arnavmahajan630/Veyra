import { describe, expect, it } from "vitest";
import { describeLayers } from "./layers";

describe("describeLayers", () => {
  it("names each envelope layer, the JSON text field, and ends at the text", () => {
    expect(describeLayers([{ syslog: { variant: "auto" } }, { json: { text_field: "msg" } }])).toBe(
      "syslog → json (msg) → text",
    );
  });

  it("handles CEF and a sample with no envelope", () => {
    expect(describeLayers([{ syslog: {} }, { cef: {} }])).toBe("syslog → cef → text");
    expect(describeLayers([])).toBe("text");
  });
});
