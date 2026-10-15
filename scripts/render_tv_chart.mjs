/**
 * Vẽ biểu đồ nến kiểu TradingView bằng thư viện mã nguồn mở Lightweight Charts
 * (https://github.com/tradingview/lightweight-charts, Apache-2.0) và chụp thành PNG
 * bằng Chrome không giao diện (puppeteer-core, dùng Chrome có sẵn trên máy).
 *
 * Usage: node scripts/render_tv_chart.mjs < spec.json
 * spec = {
 *   output, width, height, scale, fontFamily, fontSize, legend, priceDecimals,
 *   candles: [{time, open, high, low, close}],              // time: 'YYYY-MM-DD'
 *   lines:   [{color, width, style, data: [{time, value}]}], // style: 0 liền, 1 chấm, 2 gạch
 *   zones:   [{from, to, color}],                          // vùng giá (kháng cự/hỗ trợ, mây Ichimoku)
 * }
 * In ra JSON {ok: true, output} hoặc {ok: false, error}.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LWC = path.join(ROOT, 'node_modules', 'lightweight-charts', 'dist',
  'lightweight-charts.standalone.production.js');

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome',
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
  ];
  const pw = '/opt/pw-browsers';
  if (fs.existsSync(pw)) {
    for (const d of fs.readdirSync(pw)) {
      candidates.push(path.join(pw, d, 'chrome-linux', 'chrome'));
    }
  }
  return candidates.find((p) => p && fs.existsSync(p));
}

function pageScript(spec) {
  /* Chạy trong trình duyệt */
  const el = document.getElementById('chart');
  const chart = LightweightCharts.createChart(el, {
    width: spec.width,
    height: spec.height,
    layout: {
      background: { type: 'solid', color: '#ffffff' },
      textColor: '#131722',
      fontFamily: spec.fontFamily,
      fontSize: spec.fontSize,
      attributionLogo: true,
    },
    grid: { vertLines: { color: '#f0f3fa' }, horzLines: { color: '#f0f3fa' } },
    rightPriceScale: { borderColor: '#d1d4dc', scaleMargins: { top: spec.topMargin || 0.18, bottom: 0.08 } },
    timeScale: {
      borderColor: '#d1d4dc', timeVisible: false, rightOffset: 2, fixLeftEdge: true,
      tickMarkFormatter: (t) => {
        let d; let m;
        if (typeof t === 'string') { [, m, d] = t.split('-'); }
        else if (typeof t === 'number') { const x = new Date(t * 1000); d = x.getUTCDate(); m = x.getUTCMonth() + 1; }
        else { d = t.day; m = t.month; }
        return `${String(d).padStart(2, '0')}/${String(m).padStart(2, '0')}`;
      },
    },
    localization: {
      locale: 'vi-VN',
      dateFormat: 'dd/MM/yyyy',
    },
    crosshair: { mode: 2 },
    handleScroll: false,
    handleScale: false,
  });
  const fmt = { type: 'price', precision: spec.priceDecimals, minMove: Math.pow(10, -spec.priceDecimals) };

  for (const z of spec.zones || []) {
    const top = Math.max(z.from, z.to);
    const bottom = Math.min(z.from, z.to);
    const s = chart.addBaselineSeries({
      baseValue: { type: 'price', price: bottom },
      topFillColor1: z.color, topFillColor2: z.color, topLineColor: 'rgba(0,0,0,0)',
      bottomFillColor1: 'rgba(0,0,0,0)', bottomFillColor2: 'rgba(0,0,0,0)', bottomLineColor: 'rgba(0,0,0,0)',
      lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      priceFormat: fmt,
    });
    s.setData(spec.candles.map((c) => ({ time: c.time, value: top })));
  }
  const candles = chart.addCandlestickSeries({
    upColor: '#089981', downColor: '#f23645', borderVisible: false,
    wickUpColor: '#089981', wickDownColor: '#f23645', priceFormat: fmt,
  });
  candles.setData(spec.candles);
  for (const l of spec.lines || []) {
    const s = chart.addLineSeries({
      color: l.color, lineWidth: l.width || 1, lineStyle: l.style || 0,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: fmt,
    });
    s.setData(l.data);
  }
  chart.timeScale().fitContent();
  const legend = document.getElementById('legend');
  legend.innerHTML = spec.legend || '';
}

async function main() {
  const spec = JSON.parse(fs.readFileSync(0, 'utf8'));
  const chrome = findChrome();
  if (!chrome) throw new Error('Không tìm thấy Chrome/Chromium (đặt biến CHROME_PATH)');
  const browser = await puppeteer.launch({
    executablePath: chrome,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--font-render-hinting=none'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: spec.width, height: spec.height, deviceScaleFactor: spec.scale || 3 });
    await page.setContent(`<!doctype html><html><head><meta charset="utf-8"><style>
      html,body{margin:0;padding:0;background:#fff}
      #wrap{position:relative;width:${spec.width}px;height:${spec.height}px}
      #legend{position:absolute;left:4px;top:3px;z-index:5;background:rgba(255,255,255,0.85);padding:0 3px;font-family:${JSON.stringify(spec.fontFamily)};
        font-size:${spec.fontSize}px;line-height:1.25;color:#131722;pointer-events:none}
      #legend span{white-space:nowrap}
    </style></head><body><div id="wrap"><div id="legend"></div><div id="chart"></div></div></body></html>`);
    await page.addScriptTag({ path: LWC });
    await page.evaluate(pageScript, spec);
    await new Promise((r) => setTimeout(r, 250));
    const wrap = await page.$('#wrap');
    fs.mkdirSync(path.dirname(spec.output), { recursive: true });
    await wrap.screenshot({ path: spec.output });
    process.stdout.write(JSON.stringify({ ok: true, output: spec.output }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
  process.exit(1);
});
