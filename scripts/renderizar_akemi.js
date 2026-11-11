// Converte os SVGs de assets/akemi em PNGs transparentes (para AVATAR_SKIN).
// Uso: node scripts/renderizar_akemi.js [escala]   (precisa do playwright)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const dir = path.join(__dirname, '..', 'assets', 'akemi');
  const scale = Number(process.argv[2] || 1);
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: scale });
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith('.svg') && f !== 'folha.svg')) {
    const svg = fs.readFileSync(path.join(dir, file), 'utf8');
    await page.setViewportSize({ width: 400, height: 560 });
    await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`);
    const out = path.join(dir, file.replace('.svg', '.png'));
    await page.locator('svg').screenshot({ path: out, omitBackground: true });
    console.log('ok', out);
  }
  await browser.close();
})();
