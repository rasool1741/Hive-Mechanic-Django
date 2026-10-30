const CACHE_NAME = 'hive-mechanic-public-v1'
const PUBLIC_ASSETS = [
  '/static/favicon/site.webmanifest',
  '/static/favicon/android-chrome-192x192.png',
  '/static/favicon/android-chrome-512x512.png'
]

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(PUBLIC_ASSETS)))
})

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))))
})

self.addEventListener('fetch', event => {
  const requestUrl = new URL(event.request.url)
  if (requestUrl.pathname.startsWith('/api/') || requestUrl.pathname.includes('/sessions/')) return
  if (requestUrl.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)))
  }
})
