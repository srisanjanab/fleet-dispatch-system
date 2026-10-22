import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser calls /api/...; Vite forwards it to the FastAPI backend and strips "/api".
// This avoids CORS problems during development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
