import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/auth": "http://127.0.0.1:8001",
      "/chat": "http://127.0.0.1:8001",
      "/charts": "http://127.0.0.1:8001",
      "/memory": "http://127.0.0.1:8001",
      "/tools": "http://127.0.0.1:8001"
    }
  }
});
