import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies all /api traffic (REST + SSE) to the FastAPI backend.
// In production the backend serves the built `dist/` directly, so the same
// relative "/api" base works in both environments.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8888,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8848",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
