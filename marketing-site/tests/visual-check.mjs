import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";

const output = fileURLToPath(new URL("../artifacts/", import.meta.url));
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
const errors = [];

for (const viewport of [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "mobile", width: 390, height: 844 },
]) {
  const page = await browser.newPage({ viewport });
  page.on("console", (message) => { if (message.type() === "error") errors.push(`${viewport.name}: ${message.text()}`); });
  page.on("pageerror", (error) => errors.push(`${viewport.name}: ${error.message}`));
  await page.goto("http://127.0.0.1:3000", { waitUntil: "networkidle" });
  await page.screenshot({ path: join(output, `${viewport.name}-top.png`), fullPage: false });
  await page.locator("#produit").scrollIntoViewIfNeeded();
  await page.locator(".journey-count strong").waitFor({ state: "attached" });
  await page.waitForFunction(() => document.querySelector(".journey-count strong")?.textContent === "04", null, { timeout: 15000 });
  await page.waitForTimeout(1400);
  await page.screenshot({ path: join(output, `${viewport.name}-story.png`), fullPage: false });
  await page.locator("#metiers").scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const profileButtons = page.locator(".profile-list button");
  if (await profileButtons.count() > 1) {
    await profileButtons.nth(1).click();
    await page.waitForTimeout(150);
  }
  const activeProfile = (await page.locator(".profile-list button.active").textContent())?.trim();
  const detailProfile = (await page.locator(".profile-product h3").textContent())?.trim();
  if (!activeProfile?.includes(detailProfile || "__missing__")) errors.push(`${viewport.name}: active profile and detail disagree`);
  await page.screenshot({ path: join(output, `${viewport.name}-profiles.png`), fullPage: false });
  const metrics = await page.evaluate(() => ({
    title: document.title,
    h1: document.querySelector("h1")?.textContent?.trim(),
    horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    links: document.querySelectorAll("a").length,
    images: [...document.images].every((image) => image.complete && image.naturalWidth > 0),
  }));
  console.log(JSON.stringify({ viewport: viewport.name, ...metrics }));
  await page.close();
}

await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exitCode = 1;
}
