import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetMockState } from "../mocks/handlers";
import { server } from "./server";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetMockState();
  // Absent in files that opt into the node environment (the contrast check).
  if (typeof localStorage !== "undefined") localStorage.clear();
});

afterAll(() => server.close());
