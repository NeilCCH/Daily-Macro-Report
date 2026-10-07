// Screenshot an HTML card into PNG (+ a downscaled <1MB preview) using the
// pre-installed headless Chromium via Playwright. No network / no Pillow.
//
// Usage: node shot_card.js card.html card.png card_preview.png
const { chromium } = require('playwright');

(async () => {
  const [, , htmlPath, outPng, previewPng] = process.argv;
  if (!htmlPath || !outPng) {
    console.error('usage: node shot_card.js card.html card.png [card_preview.png]');
    process.exit(2);
  }
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ deviceScaleFactor: 2 });
    await page.goto('file://' + require('path').resolve(htmlPath));
    await page.evaluate(() => document.fonts.ready);
    const card = await page.$('#card');
    if (!card) {
      console.error('shot_card: #card element not found in ' + htmlPath);
      process.exitCode = 1;
      return;
    }
    await card.screenshot({ path: outPng });

    if (previewPng) {
      // Layout width 480 css px; with deviceScaleFactor 2 the file is 960px wide
      // (still well under LINE's 1 MB preview limit).
      const box = await card.boundingBox();
      const scale = 480 / box.width;
      await page.setViewportSize({ width: 480, height: Math.ceil(box.height * scale) });
      await page.addStyleTag({ content: `#card{transform:scale(${scale});transform-origin:top left;}` });
      await page.screenshot({
        path: previewPng,
        clip: { x: 0, y: 0, width: 480, height: Math.ceil(box.height * scale) },
      });
    }
    console.log('wrote', outPng, previewPng || '');
  } catch (err) {
    console.error('shot_card failed:', err.message);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
})();
