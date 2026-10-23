// End-to-end: both versions in headless Chromium, with the camera replaced by a stream that shows a
// receipt photo of your choice at its full resolution.
//
//   node e2e.js path/to/receipt.jpg [more.jpg ...]
//
// The app must be running (python serve.py); set APP_URL to test another address, e.g. the deployed
// app. Version 2: takes a picture at every step and confirms what is offered first, adds the items
// twice (the second picture overlaps completely, so nothing may be added twice) and checks the
// summary. Version 1: live-scans the same photo until a total shows. Screenshots go to tests/output/e2e.
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const OUT = path.join(__dirname, "../output");

// Replaces getUserMedia in every frame with a canvas stream showing the photo. (Chrome's own fake
// camera crops frames to 1080 px high, which cuts a long receipt photo to its middle third.)
function fakeCamera({ src }) {
  navigator.mediaDevices.getUserMedia = async () => {
    const img = new Image();
    img.src = src;
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    const paint = () => ctx.drawImage(img, 0, 0);
    paint();
    setInterval(paint, 100); // keep frames coming
    return canvas.captureStream(10);
  };
}

async function run(photo) {
  const failures = [];
  const check = (ok, what) => {
    console.log(`${ok ? "  ok  " : "  FAIL"} ${what}`);
    if (!ok) failures.push(what);
  };
  const shots = path.join(OUT, "e2e", path.basename(photo).replace(/\.\w+$/, ""));
  fs.mkdirSync(shots, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 2 });
  const type = /\.png$/i.test(photo) ? "png" : "jpeg";
  await page.addInitScript(fakeCamera, { src: `data:image/${type};base64,${fs.readFileSync(photo).toString("base64")}` });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

  await page.goto(process.env.APP_URL || "http://localhost:8501/", { waitUntil: "domcontentloaded" });
  // generous: a fresh server downloads the OCR models on the first visit
  let frameEl = await page.waitForSelector("iframe[title*='receipt_scanner']", { timeout: 240000 });
  let frame = await frameEl.contentFrame();
  // the settings sidebar opens on the first visit; on a phone-sized screen it covers the page
  check(await page.getByText("Scanner settings").isVisible(), "scanner settings open before the first scan");
  await page.locator("[data-testid='stSidebarCollapseButton']").first().click();

  // Click a version in the switch and wait for its camera component (#pause: Version 1, #shoot: Version 2).
  async function openVersion(label, marker) {
    await page.getByText(label, { exact: true }).click();
    for (let t = 0; t < 60; t++) {
      const el = await page.$("iframe[title*='receipt_scanner']");
      const f = el && (await el.contentFrame());
      if (f && (await f.$(marker).catch(() => null))) return [el, f];
      await page.waitForTimeout(500);
    }
    throw new Error(`${label} did not open`);
  }
  check(await frame.waitForSelector("#pause", { timeout: 30000 }).then(() => true, () => false), "version 1 opens first");
  [frameEl, frame] = await openVersion("Version 2 · Guided photos", "#shoot");
  await frame.waitForFunction(() => document.getElementById("status").textContent.startsWith("Ready"), null, { timeout: 90000 });
  await frame.click("#start-big");

  const main = () => page.evaluate(() => (document.querySelector("[data-testid='stMain']") || document.body).innerText);
  const button = (name) => page.getByRole("button", { name, exact: true });
  // the first option listed under a question (a line of its own) in the page text
  const firstOffered = (text, question) => {
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
    const at = lines.indexOf(question);
    return at >= 0 && at + 1 < lines.length ? lines[at + 1] : "";
  };
  async function click(name) {
    try {
      await button(name).click({ timeout: 20000 });
    } catch (err) {
      console.log(`---- no "${name}" button; page shows:\n${await main()}`);
      throw err;
    }
  }

  // Take a picture on step `stepId` and wait until Streamlit has outlined what it found.
  async function shoot(stepId, shot) {
    await frame.waitForFunction((id) => window.__scanner.step.id === id && window.__scanner.mode === "camera", stepId, { timeout: 30000 });
    await frame.waitForSelector("#shoot:not([disabled])", { timeout: 30000 });
    const t0 = Date.now();
    await frame.click("#shoot");
    await frame.waitForFunction(() => window.__scanner.mode === "review" && window.__scanner.review, null, { timeout: 90000 });
    await page.waitForTimeout(800);
    const p = await frame.evaluate(() => ({ w: window.__scanner.photo.w, h: window.__scanner.photo.h, lines: window.__scanner.photo.lines.length }));
    console.log(`${stepId}: ${p.w}x${p.h} photo, ${p.lines} lines read (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
    await frameEl.screenshot({ path: path.join(shots, `${shot}.png`) });
    return main();
  }

  let text = await shoot("vendor", "1_vendor");
  console.log(`      store name offered: ${JSON.stringify(firstOffered(text, "Which one is the store name?"))}`);
  await click(/Which one is the store name\?\s*\n\s*✏️/.test(text) ? "Skip" : "Confirm");

  text = await shoot("datetime", "2_datetime");
  console.log(`      date/time offered: ${firstOffered(text, "Date")} ${firstOffered(text, "Time")}`);
  await click(/No date or time found/.test(text) ? "Skip" : "Confirm");

  text = await shoot("total", "3_total");
  const offered = firstOffered(text, "Which is the total?");
  const total = parseFloat(offered) || 1;
  console.log(`      total offered: ${offered}`);
  // typing the total instead of picking it must work too (this path once crashed with a KeyError)
  await page.getByText("✏️ Type it myself").click();
  const typed = page.getByLabel("Type the total");
  await typed.fill(String(total));
  await typed.press("Enter");
  await page.waitForTimeout(1200);
  await click("Confirm");
  await page.waitForTimeout(1500);
  text = await main();
  check(text.includes("Step 4 of") && !/Error|Traceback/.test(text), "typed total accepted, next step shown");

  text = await shoot("count", "4_count");
  await click(/How many items does the receipt say\?\s*\n\s*\d/.test(text) ? "Confirm" : "Skip");

  await click("Yes, scan items");
  text = await shoot("items", "5_items");
  const add = (text.match(/Add (\d+) items?/) || [])[1];
  if (Number(add) > 0) {
    await click(`Add ${add} item${add === "1" ? "" : "s"}`);
    await page.waitForTimeout(1500);
    check((await main()).includes(`${add} items so far`), `${add} items added`);
    // the same view again: everything overlaps, nothing may be added twice
    text = await shoot("items", "6_items_again");
    check(/Add 0 items/.test(text), "a fully overlapping picture adds nothing");
  } else {
    console.log("      no items found in this photo");
  }
  await click("Done with items");

  await page.waitForTimeout(2500);
  text = await main();
  await page.screenshot({ path: path.join(shots, "7_summary.png"), fullPage: true });
  check(text.includes("Step 7 of 7") && !/Error|Traceback/.test(text), "summary shown");
  console.log(`      summary: ${(text.match(/(✓|⚠)[^\n]*\n?[^\n]*/) || ["(no check, items not scanned)"])[0].replace(/\s+/g, " ").trim()}`);
  check(await frame.evaluate(() => !window.__scanner.cameraOn), "camera released once scanning is finished");
  check(await button("Download JSON (doesn't work)").isDisabled(), "JSON download turned off");

  // Version 1 (live scan) on the same photo, then back to Version 2
  [frameEl, frame] = await openVersion("Version 1 · Live scan", "#pause");
  await frame.waitForFunction(() => document.getElementById("status").textContent.startsWith("Ready"), null, { timeout: 90000 });
  await frame.click("#start-big");
  const t1 = Date.now();
  await page.waitForFunction(() => /Total\s+\d/.test((document.querySelector("[data-testid='stMain']") || document.body).innerText),
    null, { timeout: 90000 }).catch(() => {});
  text = await main();
  await page.screenshot({ path: path.join(shots, "8_version1.png"), fullPage: true });
  const kept = await frame.evaluate(() => window.__scanner.doc.lines.length);
  console.log(`version 1: ${kept} lines kept after ${((Date.now() - t1) / 1000).toFixed(0)} s`);
  check(kept > 0, "version 1 reads the receipt");
  check(await button("Download JSON (doesn't work)").isDisabled(), "version 1 JSON download turned off");
  [frameEl, frame] = await openVersion("Version 2 · Guided photos", "#shoot");
  // switching versions keeps each one's progress: Version 2 comes back on its summary
  check(await page.waitForFunction(() => document.body.innerText.includes("Step 7 of 7"), null, { timeout: 30000 })
    .then(() => true, () => false), "switched back to version 2, still on its summary");

  check(errors.length === 0, `no page errors${errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""}`);
  await browser.close();
  return failures;
}

(async () => {
  const photos = process.argv.slice(2);
  if (!photos.length) {
    console.log("usage: node e2e.js path/to/receipt.jpg [more.jpg ...]");
    process.exit(2);
  }
  const failed = [];
  for (const photo of photos) {
    console.log(`\n=== ${photo}`);
    const f = await run(photo);
    if (f.length) failed.push(`${photo}: ${f.join("; ")}`);
  }
  console.log(failed.length ? `\nE2E FAILED\n${failed.join("\n")}` : "\nE2E PASSED");
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
