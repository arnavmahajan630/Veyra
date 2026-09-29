import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("veyra-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

// Beat 4 (04_DEMO_SCRIPT): drift → draft → four-eyes → promote → replay history.
test("Beat 4: a drafted parser is approved, promoted and replayed", async ({ page }) => {
  await signIn(page, "author@maha");
  const started = Date.now();

  await page.getByRole("link", { name: "Drift", exact: true }).click();
  await page.getByRole("link", { name: /FAILED login/ }).click();
  await page.getByRole("button", { name: "Submit for approval" }).click();

  // The author may not approve: the backend's refusal shows verbatim (C6 AC3).
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("this action needs one of the roles");

  await page.getByRole("button", { name: "Switch to approver@veyra" }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await page.getByRole("button", { name: "Promote", exact: true }).click();
  await page.getByRole("button", { name: "Replay 8 events" }).click();

  const done = page.getByRole("link", { name: "8 events replayed. View in Lineage" });
  await expect(done).toBeVisible({ timeout: 15_000 });
  await expect(done).toHaveAttribute("href", /q=t_3c85a1bfbf81/);

  expect(Date.now() - started).toBeLessThan(40_000); // C6 AC2, mock timing
});
