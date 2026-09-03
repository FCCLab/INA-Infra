import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5181,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/docs": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/openapi.json": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/redoc": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/video": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/snapshot": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/live": { target: "http://127.0.0.1:8080", changeOrigin: true },
      "/whep": { target: "http://127.0.0.1:8080", changeOrigin: true },
    },
  },
});
