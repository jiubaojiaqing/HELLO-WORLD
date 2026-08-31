/**
 * 打包 Web 资源到 iOS 工程: static/ → ios/ChanTrading/Web/
 * 布局复刻服务器: Web/index.html + Web/manifest.json + Web/sw.js + Web/static/<其余资源>
 * 排除测试残留文件; 可重复执行(数据更新后重跑即可)
 * 用法: node pack_ios_web.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'static');
const DST = path.join(ROOT, 'ios', 'ChanTrading', 'Web');

// 放在 Web 根的文件(对应服务器根路径路由); 其余全部进 Web/static/
const ROOT_FILES = new Set(['index.html', 'manifest.json', 'sw.js']);
// 排除: 测试残留 / 开发用页面
const EXCLUDE_FILES = new Set(['_theme_test.html', 'test_lc.html', 'test_lc2.html']);

function copyAll(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const name of fs.readdirSync(src)) {
    const s = path.join(src, name);
    const st = fs.statSync(s);
    if (st.isDirectory()) copyAll(s, path.join(dst, name));
    else if (!EXCLUDE_FILES.has(name)) fs.copyFileSync(s, path.join(dst, name));
  }
}

fs.rmSync(DST, { recursive: true, force: true });
fs.mkdirSync(DST, { recursive: true });
fs.mkdirSync(path.join(DST, 'static'), { recursive: true });
for (const name of fs.readdirSync(SRC)) {
  const s = path.join(SRC, name);
  const st = fs.statSync(s);
  if (st.isDirectory()) copyAll(s, path.join(DST, 'static', name));
  else if (ROOT_FILES.has(name)) fs.copyFileSync(s, path.join(DST, name));
  else if (!EXCLUDE_FILES.has(name)) fs.copyFileSync(s, path.join(DST, 'static', name));
}

// 校验关键文件
const required = ['index.html', 'manifest.json', 'sw.js',
  'static/lightweight-charts.standalone.production.js',
  'static/engine_offline.js', 'static/data_bundle.js'];
let ok = true;
for (const f of required) {
  if (!fs.existsSync(path.join(DST, f))) { console.error('MISSING: ' + f); ok = false; }
}
const files = [];
(function walk(d) {
  for (const n of fs.readdirSync(d)) {
    const p = path.join(d, n);
    if (fs.statSync(p).isDirectory()) walk(p);
    else files.push(path.relative(DST, p));
  }
})(DST);
const sizeMB = (files.reduce((a, f) => a + fs.statSync(path.join(DST, f)).size, 0) / 1048576).toFixed(2);
console.log((ok ? 'PACK OK' : 'PACK FAILED') + '  files=' + files.length + '  size=' + sizeMB + 'MB  -> ' + DST);
if (!ok) process.exit(1);
