import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import ComponentsPage, { SAMPLES } from "./ComponentsPage";

describe("ComponentsPage (C5 AC3)", () => {
  it("highlights the exact bytes of every field in each sample", () => {
    const { container } = renderWithProviders(<ComponentsPage />);
    for (const sample of SAMPLES) {
      const section = container.querySelector(`[data-sample="${sample.id}"]`) as HTMLElement;
      const marks = [...section.querySelectorAll("mark")].map((m) => m.textContent);
      expect(marks.sort()).toEqual(sample.fields.map(([, value]) => value).sort());
    }
  });

  it("draws the thread while a field is hovered", () => {
    const { container } = renderWithProviders(<ComponentsPage />);
    const section = container.querySelector('[data-sample="devanagari"]') as HTMLElement;
    fireEvent.mouseEnter(within(section).getByText("status").closest("div") as HTMLElement);
    expect(section.querySelector("path.thread-draw")).not.toBeNull();
    fireEvent.mouseLeave(within(section).getByText("status").closest("div") as HTMLElement);
    expect(section.querySelector("path.thread-draw")).toBeNull();
  });

  it("shows the building blocks", () => {
    renderWithProviders(<ComponentsPage />);
    expect(screen.getByText("Unknown template")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show a toast" })).toBeInTheDocument();
  });
});
