import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  root: frontendRoot,
  plugins: [react()],
  build: {
    outDir: resolve(frontendRoot, "dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(frontendRoot, "index.html"),
      },
    },
  },
});
