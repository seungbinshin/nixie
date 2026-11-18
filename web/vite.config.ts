/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server: the gateway (nixie gateway --auth token ...) runs on 127.0.0.1:8080.
const gateway = process.env.NIXIE_GATEWAY ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, target: "es2021" },
  server: {
    proxy: {
      "/u": { target: gateway, changeOrigin: false },
      "/auth": { target: gateway, changeOrigin: false },
    },
  },
  test: { environment: "jsdom", globals: false },
});
