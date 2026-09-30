import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { signInAs } from "../../mocks/handlers";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/server";
import { PublicKeyPanel, derFromPem } from "./PublicKeyPanel";

const PEM =
  "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAf8Kq+P7m2N3h5lG+9aQ3eK9sJ2Y1u8v7w0x9z8A1b2c=\n-----END PUBLIC KEY-----\n";

describe("derFromPem", () => {
  it("strips the armour and decodes the base64 body", () => {
    const der = derFromPem(PEM);
    // An Ed25519 SubjectPublicKeyInfo is 44 bytes and starts with a SEQUENCE tag.
    expect(der.length).toBe(44);
    expect(der[0]).toBe(0x30);
  });
});

describe("PublicKeyPanel", () => {
  it("fetches the PEM as text, not JSON, and shows it", async () => {
    // The endpoint answers text/plain; reading it with response.json() rejects every time,
    // which is what this guards against.
    signInAs("admin@veyra");
    renderWithProviders(<PublicKeyPanel />);
    expect(await screen.findByText(/BEGIN PUBLIC KEY/)).toBeInTheDocument();
  });

  it("shows the SHA-256 fingerprint of the DER", async () => {
    signInAs("admin@veyra");
    renderWithProviders(<PublicKeyPanel />);
    const fingerprint = await screen.findByTestId("pubkey-fingerprint");
    // 32 bytes, colon separated.
    expect(fingerprint.textContent?.split(":")).toHaveLength(32);
  });

  it("says the key could not be fetched instead of showing an empty block", async () => {
    signInAs("admin@veyra");
    server.use(http.get("/api/evidence/pubkey", () => new HttpResponse(null, { status: 503 })));
    renderWithProviders(<PublicKeyPanel />);
    expect(await screen.findByText(/could not be fetched/i)).toBeInTheDocument();
  });
});
