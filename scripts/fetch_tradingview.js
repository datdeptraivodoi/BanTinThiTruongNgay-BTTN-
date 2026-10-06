/**
 * Node.js bridge to fetch OHLCV daily candles from TradingView via @mathieuc/tradingview
 * Usage: node scripts/fetch_tradingview.js [symbols_json_or_comma_separated]
 */
import { getCandles } from '@mathieuc/tradingview/data';

const DEFAULT_SYMBOLS = {
  'EURUSD': 'FX:EURUSD',
  'USDJPY': 'FX:USDJPY',
  'BRENT': 'BRN1!',
  'ARABICA': 'KC1!'
};

async function fetchSymbol(symbolName, tvSymbol, count = 300) {
  try {
    const candles = await getCandles({
      symbol: tvSymbol,
      timeframe: 'D',
      count: count
    });
    return {
      success: true,
      symbol: symbolName,
      tv_symbol: tvSymbol,
      candles: candles.map(c => ({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume || 0
      }))
    };
  } catch (err) {
    return {
      success: false,
      symbol: symbolName,
      tv_symbol: tvSymbol,
      error: err.message || String(err),
      candles: []
    };
  }
}

async function main() {
  const count = 300;
  const results = {};
  
  for (const [key, tvSym] of Object.entries(DEFAULT_SYMBOLS)) {
    const res = await fetchSymbol(key, tvSym, count);
    results[key] = res;
  }
  
  // Output JSON to stdout
  process.stdout.write(JSON.stringify(results, null, 2));
}

main().catch(err => {
  process.stderr.write(JSON.stringify({ error: err.message || String(err) }));
  process.exit(1);
});
