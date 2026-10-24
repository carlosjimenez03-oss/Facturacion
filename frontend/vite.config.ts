import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Service worker (solo en el build; el servidor de desarrollo sigue sin
    // él): guarda el "app shell" -- index.html, JS, CSS e íconos del build --
    // para que el POS abra sin conexión con el servidor si ya se visitó antes
    // (ver docs/dgii/CONTINGENCIA.md, "POS sin conexión"). NO guarda
    // respuestas de las APIs: lo que el POS necesita sin conexión (catálogo,
    // caja, ventas por sincronizar) lo guarda el propio POS en IndexedDB.
    VitePWA({
      // El navegador busca un sw.js nuevo en cada carga; el nuevo toma el
      // control enseguida (skipWaiting + clientsClaim) sin recargar la página
      // (una venta a medias no se pierde): la próxima navegación ya es la
      // versión nueva.
      registerType: "autoUpdate",
      injectRegister: "script-defer",
      manifest: {
        name: "Kontara POS",
        short_name: "Kontara POS",
        description: "Facturación electrónica y punto de venta",
        lang: "es-DO",
        start_url: "/pos",
        scope: "/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#14B8A6",
        icons: [{ src: "/icono-pos.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
      },
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,ico}"],
        cleanupOutdatedCaches: true,
        skipWaiting: true,
        clientsClaim: true,
        // Las páginas se piden primero a la red (en línea todo se comporta
        // como antes: siempre el index.html recién desplegado); solo si no
        // hay red responde la copia guardada. Nunca las rutas de las APIs ni
        // la descarga del PDF/XML (que también son navegaciones), ni el
        // portal público /p/ (lleva Referrer-Policy de nginx).
        navigateFallback: null,
        runtimeCaching: [
          {
            urlPattern: ({ request, url }) =>
              request.mode === "navigate" && url.origin === self.location.origin && !/^\/(api|auth|fe|p)(\/|-|$)/.test(url.pathname),
            handler: "NetworkFirst",
            options: {
              cacheName: "kontara-paginas",
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 20 },
              cacheableResponse: { statuses: [200] },
              precacheFallback: { fallbackURL: "/index.html" },
            },
          },
          {
            // Tipografías de Google Fonts (index.html): sin red el POS se ve igual.
            urlPattern: ({ url }) => url.origin === "https://fonts.googleapis.com" || url.origin === "https://fonts.gstatic.com",
            handler: "StaleWhileRevalidate",
            options: { cacheName: "kontara-fuentes", expiration: { maxEntries: 20 }, cacheableResponse: { statuses: [0, 200] } },
          },
        ],
      },
    }),
  ],
  server: {
    host: true,
    port: 5173,
  },
  preview: {
    host: true,
    port: 4173,
  },
});
