import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import AuditPage from "./AuditPage";
import { auditCsv } from "./csv";

/** jsdom's Blob has no text(); FileReader it is. */
function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("auditCsv", () => {
  it("writes the header and quotes fields per RFC 4180", () => {
    const csv = auditCsv([
      { actor: "a@x", role: "admin", action: "note", target: 'say "hi", then', detail: "two\nlines", at: "t" },
    ]);
    expect(csv.split("\r\n")).toEqual(["actor,role,action,target,detail,at", 'a@x,admin,note,"say ""hi"", then","two\nlines",t', ""]);
  });
});

describe("AuditPage", () => {
  it("lists the audit rows and filters them by text", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<AuditPage />, { route: "/audit" });
    await screen.findByText("contract.submit");
    expect(screen.getAllByRole("row")).toHaveLength(6); // header + five rows
    await userEvent.type(screen.getByRole("searchbox", { name: "Filter" }), "promote");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByText("contract.promote")).toBeInTheDocument();
  });

  it("exports the rows as a CSV download", async () => {
    const blobs: Blob[] = [];
    vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      blobs.push(blob as Blob);
      return "blob:audit";
    });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    signInAs("admin@veyra");
    renderWithProviders(<AuditPage />, { route: "/audit" });
    await screen.findByText("contract.submit");
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(click).toHaveBeenCalledOnce();
    const text = await readBlob(blobs[0] as Blob);
    expect(text.split("\r\n")[0]).toBe("actor,role,action,target,detail,at");
    expect(text.split("\r\n")).toHaveLength(7); // header + five rows + the final CRLF
  });
});
