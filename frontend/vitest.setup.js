// api.js resolves its base URL from `window.location` when it loads. The unit
// tests run in Node, so give them a minimal window (no DOM library needed).
if (typeof globalThis.window === 'undefined') {
  globalThis.window = { location: { hostname: 'localhost', origin: 'http://localhost' } };
}
