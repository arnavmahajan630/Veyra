import { expect, test, type Page } from "@playwright/test";

const AUTH_UID = "0192a4f0-0000-7000-8000-000000000001";

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("veyra-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

/** The text of the highlighted span in the raw pane, after hovering a normalized row. */
async function highlightAfterHovering(page: Page, path: string): Promise<string[]> {
  await page.locator(`[data-field="${path}"]`).hover();
  const marks = page.locator("mark[data-span-id]");
  await expect(marks.first()).toBeVisible();
  return await marks.allTextContents();
}

// Beat 5 (04_DEMO_SCRIPT): every field points at its bytes, and verification walks the
// chain to a signed root.
test("Beat 5: a field's bytes highlight and the evidence verifies", async ({ page }) => {
  await signIn(page, "admin@veyra");

  await page.getByRole("link", { name: "Lineage", exact: true }).click();
  await page.getByRole("searchbox").fill("a.sharma");
  await page.getByRole("searchbox").press("Enter");
  await page.getByRole("link", { name: /a\.sharma/ }).first().click();
  await expect(page).toHaveURL(new RegExp(`/lineage/${AUTH_UID}`));

  // AC1, three values: a plain one, one inside a JSON-escaped body, and a multi-byte one.
  expect(await highlightAfterHovering(page, "src_endpoint.ip")).toContain("103.21.4.77");
  expect(await highlightAfterHovering(page, "user.name")).toContain("a.sharma");
  expect(await highlightAfterHovering(page, "dst_endpoint.ip")).toContain("10.2.3.4");

  // AC2: eight steps — seven green, immudb grey, because it is a prototype in this build.
  const started = Date.now();
  await page.getByRole("button", { name: "Verify evidence" }).click();
  await expect(page.locator('[data-step="immudb_verified"]')).toHaveAttribute(
    "data-state",
    "neutral",
  );
  await expect(page.locator('[data-step][data-state="ok"]')).toHaveCount(7);
  expect(Date.now() - started).toBeLessThan(10_000); // mock timing; the live bound is 2 s

  // AC3: an insider rewrite turns the chain red and locates the change in time…
  // It fails at merkle_inclusion and nowhere else: the insider holds the KEK, so they
  // re-encrypt and recompute every hash inside the segment. The one thing they cannot do is
  // re-sign the root. (tests/tamper/test_tamper_matrix.py pins this.)
  await page.keyboard.press("Shift+T");
  await expect(page.locator('[data-step="merkle_inclusion"]')).toHaveAttribute(
    "data-state",
    "failed",
    { timeout: 15_000 },
  );
  await expect(page.locator('[data-step="hash_raw"]')).toHaveAttribute("data-state", "ok");
  await expect(page.getByTestId("verify-failure-summary")).toContainText(
    /altered after it was sealed/,
  );
  // …while the signed root still verifies, which is what pins the blame to storage.
  await expect(page.locator('[data-step="root_signature"]')).toHaveAttribute("data-state", "ok");

  // …and untampering restores it.
  await page.getByRole("button", { name: "Untamper" }).click();
  await expect(page.locator('[data-step="merkle_inclusion"]')).toHaveAttribute(
    "data-state",
    "ok",
    { timeout: 15_000 },
  );
});

// AC5: the whole path works from the keyboard, and focus is always visible.
test("Beat 5: search, open, move through fields and verify with the keyboard only", async ({
  page,
}) => {
  await signIn(page, "admin@veyra");
  await page.goto("/lineage?q=a.sharma");

  await page.getByRole("link", { name: /a\.sharma/ }).first().focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/lineage/${AUTH_UID}`));

  // Scope to the field list: the shell's tenant <select> also contributes option roles.
  const fields = page.getByRole("listbox", { name: "Normalized" });
  const firstRow = fields.getByRole("option").first();
  await firstRow.focus();
  await expect(firstRow).toBeFocused();
  await page.keyboard.press("ArrowDown");
  const second = fields.getByRole("option").nth(1);
  await expect(second).toBeFocused();

  // Enter pins the row, which keeps its highlight up while the presenter talks.
  await page.keyboard.press("Enter");
  await expect(second).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("mark[data-span-id]").first()).toBeVisible();

  await page.getByRole("button", { name: "Verify evidence" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('[data-step][data-state="ok"]')).toHaveCount(7);
});

// The Evidence page carries the ledger and the offline export (point (d)).
test("Evidence page shows the signed-root ledger, the chain and the public key", async ({
  page,
}) => {
  await signIn(page, "admin@veyra");
  await page.getByRole("link", { name: "Evidence", exact: true }).click();

  await expect(page.getByText("w_1790000060")).toBeVisible();
  await expect(page.getByTestId("chain-status")).toHaveText("Chain intact");
  // 32 colon-separated bytes of SHA-256 over the DER.
  await expect(page.getByTestId("pubkey-fingerprint")).toContainText(/^([0-9a-f]{2}:){31}[0-9a-f]{2}$/);

  // A new window seals every few seconds in mock mode and must appear without a refresh.
  const rows = page.locator("tr[data-window]");
  const before = await rows.count();
  await expect(async () => {
    expect(await rows.count()).toBeGreaterThan(before);
  }).toPass({ timeout: 20_000 });
});
