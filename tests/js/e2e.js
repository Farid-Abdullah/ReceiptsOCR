// End-to-end: the real app in headless Chromium, with a fake camera playing a receipt-panning video.
//
//   node e2e.js [video.mjpeg] [seconds] [expected total] [ok|mismatch]
//
// The app must be running (streamlit run serve.py); set APP_URL to test another address, e.g. the
// deployed Space. Writes screenshots to tests/data/e2e and exits non-zero
// when the expectations are not met.
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const video = path.resolve(process.argv[2] || path.join(__dirname, "../data/receipt_pan_slow.mjpeg"));
const seconds = +(process.argv[3] || 40);
const expectTotal = process.argv[4];
const expectStatus = process.argv[5] || "ok";
const out = path.join(__dirname, "../data/e2e");
fs.mkdirSync(out, { recursive: true });

const mainText = (page) =>
  page.evaluate(() => (document.querySelector("[data-testid='stMain']") || document.body).innerText);

(async () => {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-video-capture=${video}`],
  });
  const page = await browser.newPage({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2 });
  const logs = [];
  page.on("console", (m) => logs.push(`[${m.type()}] ${m.text()}`));
  page.on("pageerror", (e) => logs.push(`[pageerror] ${e.message}`));

  await page.goto(process.env.APP_URL || "http://localhost:8501/", { waitUntil: "domcontentloaded" });
  // generous: a fresh server downloads the OCR models on the first visit
  const frameEl = await page.waitForSelector("iframe[title*='receipt_scanner']", { timeout: 240000 });
  const frame = await frameEl.contentFrame();
  await frame.waitForFunction(() => document.getElementById("status").textContent.startsWith("Ready"), null, { timeout: 60000 });
  console.log("isolated:", await page.evaluate(() => self.crossOriginIsolated), "/ iframe:", await frame.evaluate(() => self.crossOriginIsolated), "/ code version:", await frame.evaluate(() => window.SCANNER_VERSION));

  const t0 = Date.now();
  await frame.click("#start-big");
  for (let s = 0; s < seconds; s += 5) {
    await page.waitForTimeout(5000);
    const hud = await frame.textContent("#hud");
    const hint = await frame.textContent("#hint");
    const st = await frame.evaluate(() => {
      const sc = window.__scanner;
      return sc && sc.lastFrame
        ? { breaks: sc.motion.breaks, state: sc.lastFrame.state, lines: sc.doc.lines.length }
        : null;
    });
    console.log(`${((Date.now() - t0) / 1000).toFixed(0)}s  ${hud} | ${hint} | ${JSON.stringify(st)}`);
    await frameEl.screenshot({ path: path.join(out, `overlay_${String(s).padStart(2, "0")}.png`) });
  }
  await page.screenshot({ path: path.join(out, "page.png"), fullPage: true });
  const text = await mainText(page);
  fs.writeFileSync(path.join(out, "page.txt"), text);
  console.log("---- page text ----\n" + text.slice(0, 1500));
  const dbg = logs.filter((l) => l.includes("[scanner]"));
  if (dbg.length) console.log("---- scanner debug ----\n" + dbg.join("\n"));
  const errs = logs.filter((l) => /\[error\]|pageerror/i.test(l));
  if (errs.length) console.log("---- console errors ----\n" + errs.slice(0, 20).join("\n"));

  const failures = [];
  if (expectTotal && !new RegExp(`Total\\s+${expectTotal.replace(".", "\\.")}`).test(text)) failures.push(`total ${expectTotal} not shown`);
  if (expectStatus === "ok" && !text.includes("✓ Items")) failures.push("validation did not pass");
  if (expectStatus === "mismatch" && !text.includes("⚠")) failures.push("mismatch not flagged");
  if (errs.length) failures.push("console errors");

  // "New receipt" in the Streamlit UI must start a new scan session (the camera keeps scanning after)
  const sessionBefore = await frame.evaluate(() => window.__scanner.doc.session);
  await page.getByRole("button", { name: "New receipt" }).last().click();
  await page.waitForTimeout(4000);
  const after = await mainText(page);
  const sessionAfter = await frame.evaluate(() => window.__scanner.doc.session);
  console.log(`after reset: session ${sessionBefore} -> ${sessionAfter}`);
  if (/Exception|Traceback/.test(after)) failures.push("exception after reset");
  if (sessionAfter === sessionBefore) failures.push("scanner not reset");

  await browser.close();
  console.log(failures.length ? "E2E FAILED: " + failures.join("; ") : "E2E PASSED");
  process.exit(failures.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
