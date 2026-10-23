// End-to-end: the guided scan in headless Chromium, with a fake camera showing a receipt.
//
//   node e2e.js [video.mjpeg] [expected total] [ok|mismatch]
//
// The app must be running (streamlit run serve.py); set APP_URL to test another address, e.g. the
// deployed app. Takes a picture at every step, confirms what was found, adds the items twice (the
// second picture overlaps completely, so nothing may be added twice) and checks the summary.
// Writes screenshots to tests/data/e2e and exits non-zero on failure.
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const video = path.resolve(process.argv[2] || path.join(__dirname, "../data/receipt_still.mjpeg"));
const expectTotal = process.argv[3] || "41.51";
const expectStatus = process.argv[4] || "ok";
const out = path.join(__dirname, "../data/e2e");
fs.mkdirSync(out, { recursive: true });

const failures = [];
const check = (ok, what) => {
  console.log(`${ok ? "  ok  " : "  FAIL"} ${what}`);
  if (!ok) failures.push(what);
};

(async () => {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-video-capture=${video}`],
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await page.goto(process.env.APP_URL || "http://localhost:8501/", { waitUntil: "domcontentloaded" });
  // generous: a fresh server downloads the OCR models on the first visit
  const frameEl = await page.waitForSelector("iframe[title*='receipt_scanner']", { timeout: 240000 });
  const frame = await frameEl.contentFrame();
  await frame.waitForFunction(() => document.getElementById("status").textContent.startsWith("Ready"), null, { timeout: 90000 });
  await frame.click("#start-big");

  const main = () => page.evaluate(() => (document.querySelector("[data-testid='stMain']") || document.body).innerText);
  const button = (name) => page.getByRole("button", { name, exact: true });
  async function click(name) {
    try {
      await button(name).click({ timeout: 20000 });
    } catch (err) {
      console.log(`---- no "${name}" button; page shows:
${await main()}`);
      throw err;
    }
  }

  // Take a picture on step `stepId` and wait until Streamlit has outlined what it found.
  async function shoot(stepId, shot) {
    await frame.waitForFunction((id) => window.__scanner.step.id === id && window.__scanner.mode === "camera", stepId, { timeout: 30000 });
    await frame.waitForSelector("#shoot:not([disabled])", { timeout: 30000 });
    const t0 = Date.now();
    await frame.click("#shoot");
    await frame.waitForFunction(() => window.__scanner.mode === "review" && window.__scanner.review, null, { timeout: 60000 });
    await page.waitForTimeout(800);
    const found = await frame.evaluate(() => ({ ids: window.__scanner.review.ids.length, lines: window.__scanner.photo.lines.length }));
    console.log(`${stepId}: ${found.lines} lines read, ${found.ids} outlined (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    await frameEl.screenshot({ path: path.join(out, `${shot}.png`) });
    return found;
  }

  let text;
  let f = await shoot("vendor", "1_vendor");
  check(f.ids > 0, "store name outlined on the picture");
  text = await main();
  const offered = (text.split("store name?")[1] || "").trim().split("\n")[0];
  check(offered === "FRESH MART", `FRESH MART offered first (got ${JSON.stringify(offered)})`);
  await click("Confirm");

  f = await shoot("datetime", "2_datetime");
  text = await main();
  check(text.includes("10/21/2026") && text.includes("14:35"), "date 10/21/2026 and time 14:35 found");
  await click("Confirm");

  f = await shoot("total", "3_total");
  text = await main();
  check(new RegExp(`Which is the total\\?\\s*\\n?\\s*${expectTotal.replace(".", "\\.")}`).test(text), `total ${expectTotal} offered first`);
  check(text.includes("subtotal 39.53") && text.includes("tax 1.98"), "subtotal and tax read alongside");
  await click("Confirm");

  f = await shoot("count", "4_count");
  check(/How many items does the receipt say\?\s*\n?\s*15/.test(await main()), "item count 15 found");
  await click("Confirm");

  await click("Yes, scan items");
  f = await shoot("items", "5_items");
  check(f.ids > 0, "item rows outlined");
  await click("Add 10 items");
  await page.waitForTimeout(1500);
  check((await main()).includes("10 items so far"), "10 items added");

  // the same view again: everything overlaps, nothing new may be added
  await shoot("items", "6_items_again");
  text = await main();
  check(text.includes("10 of these were already added"), "overlap detected");
  check(await button("Add 0 items").isDisabled(), "nothing to add twice");
  await click("Done with items");

  await page.waitForTimeout(2500);
  text = await main();
  await page.screenshot({ path: path.join(out, "7_summary.png"), fullPage: true });
  fs.writeFileSync(path.join(out, "summary.txt"), text);
  if (expectStatus === "ok") check(text.includes("✓ Items 39.53 + tax 1.98 = total 41.51"), "summary: items add up");
  else check(text.includes("⚠") && text.includes(`Items 39.53 + tax 1.98 != total ${expectTotal}`), "summary: mismatch flagged");

  await click("Scan another receipt");
  await page.waitForTimeout(2500);
  check((await main()).includes("Step 1 of"), "new receipt starts at step 1");

  check(errors.length === 0, `no page errors${errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""}`);
  await browser.close();
  console.log(failures.length ? "E2E FAILED: " + failures.join("; ") : "E2E PASSED");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
