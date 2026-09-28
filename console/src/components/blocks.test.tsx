import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n/i18n";
import { ConfirmDialog } from "./ConfirmDialog";
import { DataTable, type Column } from "./DataTable";
import { Drawer } from "./Drawer";
import { TOAST_MS, ToastProvider, useToast } from "./Toast";

interface Row {
  id: string;
  eps: number;
}

const ROWS: Row[] = [
  { id: "src_b", eps: 9 },
  { id: "src_a", eps: 6 },
  { id: "src_c", eps: 12 },
];

const COLUMNS: Column<Row>[] = [
  { id: "id", header: "Source", cell: (r) => r.id, sortValue: (r) => r.id },
  { id: "eps", header: "Events/s", cell: (r) => r.eps, sortValue: (r) => r.eps, align: "right" },
];

const firstColumn = () =>
  screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0]?.textContent);

describe("DataTable", () => {
  it("sorts on header click, ascending then descending", async () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} />);
    const header = screen.getByRole("button", { name: "Events/s" });
    await userEvent.click(header);
    expect(firstColumn()).toEqual(["src_a", "src_b", "src_c"]);
    expect(screen.getByRole("columnheader", { name: "Events/s" })).toHaveAttribute("aria-sort", "ascending");
    await userEvent.click(header);
    expect(firstColumn()).toEqual(["src_c", "src_b", "src_a"]);
  });

  it("moves focus with the arrow keys and activates with Enter", async () => {
    const onActivate = vi.fn();
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} onActivate={onActivate} />);
    const rows = screen.getAllByRole("row").slice(1);
    (rows[0] as HTMLElement).focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(rows[1]).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onActivate).toHaveBeenCalledWith(ROWS[1]);
  });
});

describe("Drawer and ConfirmDialog", () => {
  it("Drawer shows its title and closes", async () => {
    const onOpenChange = vi.fn();
    render(
      <I18nProvider>
        <Drawer open onOpenChange={onOpenChange} title="src_fw_dmz_01">
          <p>details</p>
        </Drawer>
      </I18nProvider>,
    );
    expect(screen.getByRole("dialog", { name: "src_fw_dmz_01" })).toHaveTextContent("details");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("ConfirmDialog confirms and cancels", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <I18nProvider>
        <ConfirmDialog open title="Revoke key" body="Sure?" confirmLabel="Revoke" onConfirm={onConfirm} onCancel={onCancel} />
      </I18nProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });
});

function Pusher() {
  const push = useToast();
  return (
    <button type="button" onClick={() => push("Contract authsrv changed")}>
      push
    </button>
  );
}

describe("Toast", () => {
  it("shows a message and removes it after TOAST_MS", () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <Pusher />
      </ToastProvider>,
    );
    act(() => screen.getByRole("button", { name: "push" }).click());
    expect(screen.getByRole("status")).toHaveTextContent("Contract authsrv changed");
    act(() => vi.advanceTimersByTime(TOAST_MS));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    vi.useRealTimers();
  });
});
