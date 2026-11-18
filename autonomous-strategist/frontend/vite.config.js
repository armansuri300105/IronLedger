import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true, // Bind to all interfaces for tunnel access
    allowedHosts: true, // Allow Cloudflare hostnames
    port: 5173, // Default frontend port
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000', // Replace 8000 with the actual backend port
        changeOrigin: true,
        secure: false
      }
    }
  }
})