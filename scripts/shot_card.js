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
  const page = await browser.newPage({ deviceScaleFactor: 2 });
  await page.goto('file://' + require('path').resolve(htmlPath));
  const card = await page.$('#card');
  await card.screenshot({ path: outPng });

  if (previewPng) {
    // Downscale to <=1MB by capping width at 480px for the LINE preview.
    const box = await card.boundingBox();
    const scale = 480 / box.width;
    await page.setViewportSize({ width: 480, height: Math.ceil(box.height * scale) });
    await page.addStyleTag({ content: `#card{transform:scale(${scale});transform-origin:top left;}` });
    await page.screenshot({
      path: previewPng,
      clip: { x: 0, y: 0, width: 480, height: Math.ceil(box.height * scale) },
    });
  }
  await browser.close();
  console.log('wrote', outPng, previewPng || '');
})();
