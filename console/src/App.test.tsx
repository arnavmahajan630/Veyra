import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("starts at the login page when nobody is signed in", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Sign in to VEYRA" })).toBeInTheDocument();
  });
});
