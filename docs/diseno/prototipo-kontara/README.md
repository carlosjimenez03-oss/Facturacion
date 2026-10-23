# Kontara · Facturación electrónica e-CF + ERP (prototipo)

Prototipo visual para República Dominicana (Ley 32-23 / DGII). Sin backend: los datos son de ejemplo.

## Contenido
- `pantallas/` — las 47 pantallas en formato Design Component (`.dc.html`), más:
  - `app.css` — sistema visual compartido (colores, botones, tablas, badges, etc.)
  - `canvas.json` — índice del lienzo (posición y nombre de cada pantalla)
  - `Sidebar.dc.html` y `Topbar.dc.html` — menú lateral y barra superior, reutilizados por todas
  - `Modulo.dc.html` — módulos de Ingresos, Compras e Inventario; los archivos pequeños
    (NotasCredito, Productos, etc.) solo lo abren en la pestaña correspondiente
- `archivos-ejemplo/` — los archivos que generan los botones de exportar (TXT 606/607/608, PDF, XML, CSV)

## Cómo verlo
Las pantallas usan el runtime del lienzo de diseño de claude.ai (`support.js`), que no va incluido.
Para verlas, ábralas en el lienzo original o pida que se conviertan en una app web independiente
(HTML + JS, React o similar) para alojarla en su propio servidor.

## Pantalla de entrada
`pantallas/Main.dc.html` (Inicio).

## Lienzo original
https://claude.ai/artifact/FDjYJm9UDKMAxaQdtMXTme (privado; compártalo desde el menú Share para que otros lo vean).

## Relación con `frontend/`
Este prototipo es la referencia visual y de flujos para el `frontend/` en React. No se compila ni se
despliega; los componentes reales viven en `frontend/src/`.
