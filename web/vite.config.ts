import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Two builds:
// - default: the real app, served by the Python server from web/dist.
// - mock:    a single-file clickable mock with fake data (published as an artifact).
//            React comes from a CDN script tag, so it's kept out of the bundle.
export default defineConfig(({ mode }) => {
  if (mode === 'mock') {
    return {
      plugins: [react()],
      define: {
        'process.env.NODE_ENV': '"production"',
        __BC_MOCK__: 'true',
      },
      build: {
        outDir: 'dist-mock',
        emptyOutDir: true,
        cssCodeSplit: false,
        lib: {
          entry: 'src/main.tsx',
          name: 'Backchannel',
          formats: ['iife'],
          fileName: () => 'app.js',
        },
        rollupOptions: {
          external: ['react', 'react-dom', 'react-dom/client'],
          output: {
            globals: { react: 'React', 'react-dom': 'ReactDOM', 'react-dom/client': 'ReactDOM' },
            assetFileNames: 'app.[ext]',
          },
        },
      },
    }
  }
  return {
    plugins: [react()],
    define: { __BC_MOCK__: 'false' },
    server: {
      port: 5173,
      proxy: { '/api': { target: 'http://127.0.0.1:5556', changeOrigin: true } },
    },
    build: { outDir: 'dist', emptyOutDir: true },
  }
})
