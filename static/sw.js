// Service Worker for AI Chat Translator PWA
// Caches the app shell so it loads instantly and survives brief signal drops

const CACHE_NAME = "translator-v1"

// App shell — everything needed to render the UI
const SHELL = [
  "/",
  "/static/index.html",
  "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"
]

// ─────────────────────────────────────────
// INSTALL — cache the app shell
// ─────────────────────────────────────────

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL))
  )
  // Activate immediately, don't wait for old tabs to close
  self.skipWaiting()
})

// ─────────────────────────────────────────
// ACTIVATE — clean up old caches
// ─────────────────────────────────────────

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  )
  self.clients.claim()
})

// ─────────────────────────────────────────
// FETCH — network first, fall back to cache
// Strategy: always try network so content stays fresh.
// If offline, serve from cache so the UI still loads.
// WebSocket connections bypass the service worker entirely.
// ─────────────────────────────────────────

self.addEventListener("fetch", (event) => {
  // Skip non-GET and WebSocket requests
  if (event.request.method !== "GET") return
  if (event.request.url.startsWith("ws://") || event.request.url.startsWith("wss://")) return

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Cache a fresh copy for next time
        const clone = response.clone()
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone))
        return response
      })
      .catch(() => {
        // Network failed — serve from cache
        return caches.match(event.request).then(
          (cached) => cached || new Response("Offline", { status: 503 })
        )
      })
  )
})
