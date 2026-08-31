// 全标的批量对拍: engine_offline.js vs py_baseline_all.json
'use strict';
const fs = require('fs');
const path = require('path');
const OfflineEngine = require('../static/engine_offline.js');

const BASE = path.join(__dirname, '..', 'data');
const { results, skipped } = JSON.parse(fs.readFileSync(path.join(__dirname, 'py_baseline_all.json'), 'utf8'));

function clean(obj) {
  let s = JSON.stringify(obj);
  return JSON.parse(s.replace(/"session_id":"[0-9a-f]+"/g, '"session_id":"X"')
    .replace(/"created_at":"[^"]*"/g, '"created_at":"X"')
    .replace(/"id":"[0-9a-f]{8}"/g, '"id":"X"'));
}
function diff(a, b, p, out) {
  if (JSON.stringify(a) === JSON.stringify(b)) return;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') {
    out.push(p + ': py=' + JSON.stringify(b) + ' js=' + JSON.stringify(a));
    return;
  }
  for (const k of new Set([...Object.keys(a || {}), ...Object.keys(b || {})])) diff((a || {})[k], (b || {})[k], p + '.' + k, out);
}

let pass = 0; const fails = [];
for (const [name, base] of Object.entries(results)) {
  try {
    const ohl = OfflineEngine.parseOhlCsv(fs.readFileSync(path.join(BASE, name + '_kline.csv'), 'utf8'));
    const s25p = path.join(BASE, name + '_step25.csv');
    const s25 = fs.existsSync(s25p) ? OfflineEngine.parseStep25Csv(fs.readFileSync(s25p, 'utf8')) : [];
    const bars = OfflineEngine.mergeBars(ohl, s25);
    const segments = OfflineEngine.deriveSegments(bars);
    const zhongshu = OfflineEngine.deriveZhongshu(bars);
    const pens = OfflineEngine.extractPens(bars);
    const ws = Math.min(150, Math.max(100, bars.length - 100));
    const eng = new OfflineEngine.SimEngine({
      code: name.split('_')[0], bars, period: name.split('_')[1], initAsset: 1000000.0, feesOn: true,
      windowSize: ws, startIdx: 60, tplus1: true,
    });
    eng.advance(60);
    const buy1 = eng.buy(0.6);
    eng.advance(40);
    const sell1 = eng.sell(0.5);
    eng.advance(10);
    const buy2 = eng.buy(null, 500);
    eng.advance(100000);
    const st = eng.state();
    const actual = {
      ohl_count: ohl.length, s25_count: s25.length, bars_count: bars.length,
      segments, zhongshu, pens,
      buy1: clean(buy1), sell1: clean(sell1), buy2: clean(buy2),
      result: clean(eng.result()),
      final_status: st.status, final_position: st.position, final_cash: st.cash,
      final_trades: clean(st.trades),
      first_bar: clean(st.all_bars[0]), last_bar: clean(st.all_bars[st.all_bars.length - 1]),
      window_len: st.window.length, all_bars_len: st.all_bars.length,
      start_idx: st.start_idx,
    };
    const d = [];
    diff(actual, base, '', d);
    if (d.length) fails.push({ name, diffs: d.slice(0, 8) });
    else pass++;
  } catch (e) {
    fails.push({ name, diffs: ['EXCEPTION: ' + e.message] });
  }
}

console.log('PASS ' + pass + '/' + Object.keys(results).length + '  skipped:' + JSON.stringify(skipped));
if (fails.length) {
  for (const f of fails) {
    console.log('FAIL ' + f.name);
    f.diffs.forEach(d => console.log('   ' + d));
  }
  process.exit(1);
} else {
  console.log('ALL SYMBOLS MATCH: JS engine == Python engine');
}
