import { expect, test } from "@playwright/test";

test("sign in, watch the Overview move, find the NTRO sources", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill("admin@veyra");
  await page.getByLabel("Password").fill("veyra-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();

  const kafka = page.locator('[data-stage="kafka"]');
  await expect(kafka).toContainText("events/s");
  const before = await kafka.innerText();
  await expect.poll(() => kafka.innerText(), { timeout: 3_000 }).not.toBe(before);
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Sources" }).click();
  await expect(page.getByRole("cell", { name: /Acme NGFW \(DMZ\)/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /Linux sshd \(core\)/ })).toBeVisible();
});
