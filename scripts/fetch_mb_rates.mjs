/**
 * Đọc tỷ giá niêm yết trên trang https://www.mbbank.com.vn/ExchangeRate bằng Chrome không giao diện
 * (trang chặn request thường nhưng mở được như trình duyệt). Gọi API của chính trang cho từng ngày.
 *
 * Usage: node scripts/fetch_mb_rates.mjs 2026-10-15 2026-10-14
 * In ra JSON {ok: true, days: {"2026-10-15": <payload API>, ...}} hoặc {ok: false, error}.
 */
import fs from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

function findChrome() {
  const c = [process.env.CHROME_PATH, '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
    '/opt/google/chrome/chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  const pw = '/opt/pw-browsers';
  if (fs.existsSync(pw)) for (const d of fs.readdirSync(pw)) c.push(path.join(pw, d, 'chrome-linux', 'chrome'));
  return c.find((p) => p && fs.existsSync(p));
}

async function main() {
  const days = process.argv.slice(2);
  const chrome = findChrome();
  if (!chrome) throw new Error('Không tìm thấy Chrome (đặt CHROME_PATH)');
  const browser = await puppeteer.launch({
    executablePath: chrome, headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36');
    await page.goto('https://www.mbbank.com.vn/ExchangeRate', { waitUntil: 'networkidle2', timeout: 45000 });
    const out = await page.evaluate(async (wanted) => {
      const token = document.querySelector('input[name="__RequestVerificationToken"]')?.value || '';
      const res = {};
      for (const day of wanted) {
        try {
          const r = await fetch(`/api/getExchangeRate/${day}`, {
            headers: {
              'Accept': 'application/json, text/plain, */*',
              'X-Requested-With': 'XMLHttpRequest',
              ...(token ? { 'MB-XSRF-Token-FormOnline': token } : {}),
            },
            credentials: 'include',
          });
          res[day] = r.ok ? await r.json() : { error: `HTTP ${r.status}` };
        } catch (e) {
          res[day] = { error: String(e) };
        }
      }
      return res;
    }, days);
    process.stdout.write(JSON.stringify({ ok: true, days: out }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }));
  process.exit(1);
});
