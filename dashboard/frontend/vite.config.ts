import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5174,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY || "http://127.0.0.1:8090",
        changeOrigin: true,
      },
      "/docs": {
        target: process.env.VITE_API_PROXY || "http://127.0.0.1:8090",
        changeOrigin: true,
      },
      "/openapi.json": {
        target: process.env.VITE_API_PROXY || "http://127.0.0.1:8090",
        changeOrigin: true,
      },
    },
  },
});
