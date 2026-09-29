import { expect, test, type Page } from "@playwright/test";

async function signIn(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("veyra-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

// Beat 2 (04_DEMO_SCRIPT): samples to an active contract and a key, with four-eyes in one click.
test("Beat 2: onboard a source from pasted samples to an issued key", async ({ page }) => {
  await signIn(page, "author@maha");
  const started = Date.now();

  await page.getByRole("link", { name: "Onboard source" }).click();
  await page.getByLabel("Name").fill("Auth Server");
  await page.getByRole("button", { name: "Create source" }).click();
  await page.getByRole("button", { name: "Paste samples" }).click();
  await page.getByRole("button", { name: "Analyze" }).click();
  await expect(page.getByText("syslog → json (msg) → text")).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(2); // one compact review per message shape

  await page.getByRole("button", { name: "Create contract" }).click();
  await expect(page.getByText("Switch to approver@veyra to approve")).toBeVisible();

  await page.getByRole("button", { name: "Switch to approver@veyra" }).click();
  await page.getByRole("button", { name: "Approve and activate" }).click();
  await expect(page.getByText("auth_server v1 is active")).toBeVisible();

  await page.getByRole("button", { name: "Switch to author@maha" }).click();
  await page.getByRole("button", { name: "Issue key" }).click();
  await expect(page.getByText("Copy this secret now. It is shown only once.")).toBeVisible();

  expect(Date.now() - started).toBeLessThan(25_000); // C6 AC1, mock timing
});
