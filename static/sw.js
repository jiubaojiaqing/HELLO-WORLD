/* chan-trading PWA Service Worker
 * 策略:
 *  - /api/* 一律直连(不缓存), 保证交易/会话数据实时
 *  - /static/* 与首页: cache-first, 命中直接返回; 未命中拉取后入缓存(离线可打开)
 *  - 版本号 CACHE_VER 变更后旧缓存自动清理
 */
const CACHE_VER = 'chan-pwa-v4';
const CORE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/index.html',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-180.png',
  '/static/lightweight-charts.standalone.production.js',
  '/static/engine_offline.js',
  '/static/data_bundle.js',
  '/static/lang/zh-CN.json',
  '/static/lang/en.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_VER).then((c) => c.addAll(CORE_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VER).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;            // POST/PUT 等直连
  if (url.pathname.startsWith('/api/')) return;      // API 永不缓存
  if (url.origin !== self.location.origin) return;   // 跨域直连

  e.respondWith(
    caches.match(e.request).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((resp) => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE_VER).then((c) => c.put(e.request, clone));
        }
        return resp;
      }).catch(() => caches.match('/'));  // 离线且未缓存 → 兜底回首页(避免白屏)
    })
  );
});
