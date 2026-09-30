import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// The console is served by `admin-web/serve.py`, which reverse-proxies `/api/*`
// to the FastAPI app on the same origin. In dev, Vite does the same thing, so
// the app code has exactly one URL shape in both modes and there is no CORS
// preflight to negotiate.
//
// `base: './'` matters: the build is served from the console's root alongside
// the legacy `js/` bundle, so asset URLs must stay relative.
export default defineConfig({
  plugins: [react()],
  base: './',
  test: {
    // `jsdom`, because the regression this suite guards is about the DOM and
    // the address bar agreeing: the console rendered 找不到頁面 while
    // `location.hash` already read `#/`. A node-environment test cannot see
    // that mismatch, so it cannot catch the bug being tested for.
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    // The console ships no test helpers; these tests drive React directly.
    globals: false,
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8000',
        changeOrigin: false,
        // A short upstream timeout, for the same reason the Python proxy uses
        // one: a browser holds at most six sockets per origin, so a hung
        // upstream request parks a socket and starves the whole page.
        timeout: 3000,
      },
    },
  },
});
