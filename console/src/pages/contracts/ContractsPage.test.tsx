import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import ContractsPage from "./ContractsPage";

function Where() {
  return <p>{useLocation().pathname}</p>;
}

describe("ContractsPage", () => {
  it("lists every contract the user can see and opens one", async () => {
    signInAs("approver@veyra");
    renderWithProviders(
      <Routes>
        <Route path="/contracts" element={<ContractsPage />} />
        <Route path="/contracts/:id" element={<Where />} />
      </Routes>,
      { route: "/contracts" },
    );
    await screen.findByText("authsrv");
    expect(screen.getAllByRole("row")).toHaveLength(4); // header + three contracts
    expect(screen.getByText("acme_ngfw_cef")).toBeInTheDocument();
    await userEvent.click(screen.getByText("authsrv"));
    expect(await screen.findByText("/contracts/authsrv")).toBeInTheDocument();
  });

  it("shows a tenant user only their tenant's contracts", async () => {
    signInAs("author@maha");
    renderWithProviders(<ContractsPage />, { route: "/contracts" });
    await screen.findByText("authsrv");
    expect(screen.queryByText("linux_sshd")).not.toBeInTheDocument();
  });
});
