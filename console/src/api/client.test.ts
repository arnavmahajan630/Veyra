import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../test/server";
import { ApiError, api } from "./client";

describe("api client", () => {
  it("returns the parsed JSON body", async () => {
    server.use(http.get("/api/probe", () => HttpResponse.json({ ok: 1 })));
    await expect(api.get("/api/probe")).resolves.toEqual({ ok: 1 });
  });

  it("raises ApiError carrying the server's detail", async () => {
    server.use(
      http.get("/api/probe", () => HttpResponse.json({ detail: "sign in required" }, { status: 401 })),
    );
    const failure = api.get("/api/probe");
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({ status: 401, message: "sign in required" });
  });

  it("returns undefined for 204 No Content", async () => {
    server.use(http.post("/api/probe", () => new HttpResponse(null, { status: 204 })));
    await expect(api.post("/api/probe")).resolves.toBeUndefined();
  });
});
