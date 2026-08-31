// 对拍: engine_offline.js 与 Python 基线 (py_baseline.py 输出) 同输入比对
'use strict';
const fs = require('fs');
const path = require('path');
const OfflineEngine = require('../static/engine_offline.js');

const BASE = path.join(__dirname, '..', 'data');
const kline = fs.readFileSync(path.join(BASE, '002475_30min_kline.csv'), 'utf8');
const step25 = fs.readFileSync(path.join(BASE, '002475_30min_step25.csv'), 'utf8');
const baseline = JSON.parse(fs.readFileSync(path.join(__dirname, 'py_baseline.json'), 'utf8'));

const ohl = OfflineEngine.parseOhlCsv(kline);
const s25 = OfflineEngine.parseStep25Csv(step25);
const bars = OfflineEngine.mergeBars(ohl, s25);
const segments = OfflineEngine.deriveSegments(bars);
const zhongshu = OfflineEngine.deriveZhongshu(bars);
const pens = OfflineEngine.extractPens(bars);

const eng = new OfflineEngine.SimEngine({
  code: '002475', bars, period: '30min', initAsset: 1000000.0, feesOn: true,
  windowSize: 150, startIdx: 100, tplus1: true,
});
eng.advance(60);
const buy1 = eng.buy(0.6);
eng.advance(40);
const sell1 = eng.sell(0.5);
eng.advance(10);
const buy2 = eng.buy(null, 500);
eng.advance(10000);
const res = eng.result();
const st = eng.state();

function clean(obj) {
  let s = JSON.stringify(obj);
  s = s.replace(/"session_id":"[0-9a-f]+"/g, '"session_id":"X"')
       .replace(/"created_at":"[^"]*"/g, '"created_at":"X"')
       .replace(/"id":"[0-9a-f]{8}"/g, '"id":"X"');
  return JSON.parse(s);
}

const actual = {
  ohl_count: ohl.length, s25_count: s25.length, bars_count: bars.length,
  segments, zhongshu, pens,
  buy1: clean(buy1), sell1: clean(sell1), buy2: clean(buy2),
  result: clean(res),
  final_status: st.status,
  final_position: st.position,
  final_cash: st.cash,
  final_trades_n: st.trades.length,
  final_trades: clean(st.trades),
  first_bar: clean(st.all_bars[0]),
  last_bar: clean(st.all_bars[st.all_bars.length - 1]),
  window_len: st.window.length,
  all_bars_len: st.all_bars.length,
  start_idx: st.start_idx,
};

// 逐 key 比对
function diff(a, b, p, out) {
  if (JSON.stringify(a) === JSON.stringify(b)) return;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') {
    out.push(`${p}: py=${JSON.stringify(b)} js=${JSON.stringify(a)}`);
    return;
  }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) diff(a[k], b[k], p + '.' + k, out);
}
const diffs = [];
diff(actual, baseline, '', diffs);

if (!diffs.length) {
  console.log('PASS: JS 引擎与 Python 基线完全一致');
  console.log('  bars=' + actual.bars_count, 'segments=' + actual.segments.length,
    'zhongshu=' + actual.zhongshu.length, 'pens=' + actual.pens.length,
    'trades=' + actual.final_trades_n, 'final_asset=' + actual.result.final_asset,
    'profit_rate=' + actual.result.profit_rate);
  process.exit(0);
} else {
  console.log('FAIL: ' + diffs.length + ' 处差异');
  diffs.slice(0, 30).forEach(d => console.log('  ' + d));
  process.exit(1);
}
