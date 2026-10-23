# inventario-api: catálogo, almacenes, lotes, ajustes y Kardex

`services/inventario_api` es el backend de las pantallas de **Productos**,
**Almacenes**, **Lotes**, **Ajustes de inventario**, **Kardex valorizado**,
**Compras** y **Proveedores**, y el que descuenta el stock de las facturas
que envía `facturas-api`. El contrato (rutas, campos y tipos) es
[`frontend/src/lib/inventarioApi.ts`](../frontend/src/lib/inventarioApi.ts)
con los tipos de [`frontend/src/lib/types.ts`](../frontend/src/lib/types.ts)
(sección *Inventario*); el backend lo implementa 1:1 en
`app/schemas.py` y `app/main.py`. Cantidades, costos y montos viajan como
string decimal (`"545.0000"`), fechas como `YYYY-MM-DD` y marcas de tiempo
en ISO 8601.

## Base de datos y migraciones

- Esquema **`inventario`** (no se expone por PostgREST).
- **inventario-api crea y migra su esquema él mismo al arrancar** (igual que
  erp-api): después de conectarse aplica
  [`app/migraciones.py`](../services/inventario_api/app/migraciones.py)
  dentro de una transacción con un advisory lock (dos réplicas que arrancan
  a la vez no chocan). En Supabase no hace falta correr nada a mano.
  - `DDL_BASE` = [`db/init/004_inventario.sql`](../db/init/004_inventario.sql)
    (productos, proveedores, compras, movimientos). Solo se ejecuta si
    `inventario.movimientos` no existe (base nueva).
  - `DDL_FASE4` = [`db/init/011_inventario_almacenes_lotes.sql`](../db/init/011_inventario_almacenes_lotes.sql).
    Se ejecuta en cada arranque; todo es idempotente.
  - `tests/test_migraciones.py` verifica que el DDL de Python y los
    archivos de `db/init` no se separen.
- Al migrar una base existente: se crea el almacén **Principal**, toda la
  existencia actual (`productos.stock_actual`) queda en él y los
  movimientos viejos se marcan con ese almacén. Si alguna vez
  `stock_actual` no coincide con la suma por almacén (p. ej. lo escribió
  una versión anterior durante un despliegue), el siguiente arranque asigna
  la diferencia al principal.

### Modelo

| Tabla | Qué guarda |
|---|---|
| `productos` | + `tipo` (`bien`/`servicio`), `tasa_itbis` (`18`/`16`/`0`/`exento`), `codigo_barras` (único), `categoria`, `stock_minimo`, `maneja_lotes`. `stock_actual` sigue siendo el **total** de todos los almacenes y `costo_promedio` el promedio ponderado global. |
| `almacenes` | nombre (único sin importar mayúsculas), dirección, `es_principal` (exactamente uno), `permite_negativo`, `activo`. |
| `existencias` | cantidad por producto × almacén. Siempre suma `stock_actual`. |
| `lotes` | número (único por producto), `fecha_vencimiento`, `bloqueado`, notas. |
| `existencias_lote` | cantidad por lote × almacén. Lo que la existencia del almacén tiene de más es mercancía "sin lote". |
| `movimientos` | + `almacen_id`, `lote_id`, `referencia_linea` (NumeroLinea de la factura), `documento` (e-NCF, NCF, AJ-0001, TR-0001) y `secuencia` (orden dentro de una transacción). `referencia_tipo`: `compra`, `factura`, `ajuste`, `anulacion_ajuste`, `transferencia`, `lote`. |
| `compras` / `compra_items` | + `almacen_id` de la compra; `lote_numero` y `lote_vencimiento` por línea. |
| `ajustes` / `ajuste_items` | correlativo `AJ-0001`, almacén, motivo, estado; líneas entrada/salida con costo (entradas) y lote. |
| `transferencias` / `transferencia_items` | correlativo `TR-0001`, origen, destino; líneas con lote. |

Los correlativos usan secuencias: una operación rechazada consume número
(puede haber saltos).

## Reglas

- **Todo cambio de existencia** pasa por `app/stock.py`
  (`registrar_entrada` / `registrar_salida`), que mantienen juntos
  existencias, lotes, `stock_actual` y el costo promedio.
- **Costo**: promedio ponderado global por producto. Las entradas (compras,
  ajustes de entrada, entrada manual de lote) lo recalculan; sin existencia
  previa positiva el nuevo costo es el de la entrada. Las salidas y las
  transferencias salen al costo promedio vigente, que queda grabado en el
  movimiento.
- **Concurrencia**: cada movimiento bloquea la fila del producto
  (`FOR NO KEY UPDATE`), así que dos salidas simultáneas no sobrevenden;
  las operaciones de varias líneas bloquean sus productos en orden de id
  (sin deadlocks).
- **Existencia negativa**: transferencias y ajustes de salida la rechazan
  (409 con `faltante`) salvo en un almacén con `permite_negativo`. La
  salida por factura **no** valida (el e-CF ya se envió a la DGII); la
  validación es `validar-disponibilidad`, antes del envío.
- **Servicios**: no llevan existencia; compras y facturas los ignoran para
  el stock; ajustes, transferencias y lotes los rechazan.
- **Lotes**: estado calculado al leer — `bloqueado`, `vencido` (la fecha ya
  pasó), `por_vencer` (vence dentro de `DIAS_ALERTA_VENCIMIENTO`, 30 por
  defecto, o `?dias=`), `vigente`. Una salida sin lote de un producto que
  `maneja_lotes` se toma por **FEFO** (primero el que vence antes, sin
  vencidos ni bloqueados) y el resto de lo "sin lote"; puede generar varios
  movimientos. Un lote explícito se puede usar aunque esté vencido o
  bloqueado (ajuste por vencimiento). Registrar un lote activa
  `maneja_lotes` del producto.

## Endpoints

| Método y ruta | Uso |
|---|---|
| `GET /productos` | `q` (nombre, SKU, código de barras), `activo`, `categoria`, `tipo`, `alerta_stock` (bienes sin existencia o bajo mínimo), `limit`, `offset`. Cada producto trae `estado_stock`. |
| `GET /productos/categorias` | categorías en uso. |
| `POST /productos` | + `tipo`, `tasa_itbis`, `codigo_barras`, `categoria`, `stock_minimo`, `maneja_lotes`, `costo_unitario_inicial`, `almacen_id` (del stock inicial). |
| `PUT /productos/{id}` | los campos de la Fase 4 que no vienen se conservan. |
| `GET /productos/{id}/existencias` | reparto por almacén y lote. |
| `GET/POST /almacenes`, `GET/PUT /almacenes/{id}` | con `productos`, `cantidad`, `valor` de cada uno. `PUT` con `es_principal: true` cambia el principal; no se inactiva el principal ni un almacén con existencia. |
| `GET /almacenes/{id}/existencias` | productos del almacén (`q`, `incluir_cero`). |
| `POST /transferencias` | aplica en el acto, todo o nada; FEFO si la línea no trae lote y el destino recibe los mismos lotes. `GET /transferencias` (`almacen_id`), `GET /transferencias/{id}`. |
| `GET /lotes` | `producto_id`, `almacen_id`, `estado`, `q`, `con_existencia`, `dias`. |
| `POST /lotes`, `GET/PUT /lotes/{id}` | `cantidad_inicial` > 0 registra una entrada manual; `PUT` con `bloqueado`. |
| `GET /lotes/alertas?dias=` | por vencer y vencidos (con existencia, sin bloqueados). |
| `GET /lotes/fefo?producto_id&cantidad&almacen_id` | sugerencia FEFO: `tomas`, `sin_lote`, `faltante`. |
| `GET /ajustes`, `POST /ajustes[?aplicar=true]`, `GET/PUT/DELETE /ajustes/{id}` | motivos `conteo_fisico`, `merma`, `dano`, `vencimiento`, `otro`. `PUT`/`DELETE` solo en borrador. |
| `POST /ajustes/{id}/aplicar` | borrador → aplicado. |
| `POST /ajustes/{id}/anular` | aplicado → anulado con los movimientos inversos (mismo almacén y lote). Falla (409) si la mercancía que entró ya salió. |
| `GET /movimientos` | + `almacen_id`, `lote_id`, `referencia_tipo`, `referencia_id`, `desde`, `hasta`. |
| `GET /kardex?producto_id` | `almacen_id`, `desde`, `hasta`, `metodo` (`promedio`/`peps`), `tipo`, `referencia_tipo`, `formato=csv`. Ver abajo. |
| `GET /valuacion` | valor total, por almacén y por categoría; contadores `sin_existencia` / `bajo_minimo`. |
| `POST /movimientos/validar-disponibilidad`, `POST /movimientos/salida-por-factura` | uso interno de facturas-api (ver abajo). |

Errores: 404 no existe (también un almacén o lote referenciado en el
cuerpo), 409 la operación no aplica (estado, almacén inactivo, duplicado,
sin existencia — este último con `faltante: {producto_id, nombre,
almacen_id, lote_id, solicitado, disponible}`), 422 datos inválidos o
producto inexistente en el cuerpo.

### Kardex

`GET /kardex` re-ejecuta todos los movimientos del producto (o del producto
en un almacén) y devuelve `saldo_inicial` (todo lo anterior a `desde`),
`filas` en orden cronológico — `concepto` ("Compra", "Venta", "Ajuste de
salida", "Transferencia enviada"...), `documento`, `cantidad` y
`costo_total` con signo, `costo_unitario`, y el acumulado
`saldo_cantidad`/`costo_promedio`/`saldo_valor` —, `saldo_final` y las
cantidades `entradas`/`salidas` del período. `tipo`/`referencia_tipo` solo
filtran filas; los saldos cuentan todo.

- `promedio` usa el costo grabado en cada movimiento (las salidas al
  promedio vigente). `peps` es solo una vista: las salidas consumen las
  capas de entrada más antiguas.
- Sin `almacen_id` no se muestran las transferencias (se anulan entre sí).
- `formato=csv`: mismo contenido como archivo para Excel (UTF-8 con BOM,
  comas, punto decimal, fecha DD/MM/AAAA), con una fila de saldo inicial.

### Integración con facturas-api

Cada línea de `items_inventario` de una factura puede traer, además de
`numero_linea`, `producto_id` y `cantidad`, un **`almacen_id`** (selector
"Almacén" de la factura; sin él, el principal) y un **`lote_id`** (sin él,
FEFO si el producto maneja lotes). facturas-api los guarda y los reenvía tal
cual, junto con el e-NCF como `documento` del Kardex.

- `validar-disponibilidad` suma lo pedido por producto × almacén y por lote;
  reporta un lote vencido o bloqueado como faltante (`motivo`
  `lote_vencido`/`lote_bloqueado`). Lo pedido con lote explícito no cuenta
  para el FEFO de otra línea.
- `salida-por-factura` es **idempotente por factura**: un advisory lock por
  factura y, si ya hay salidas de esa factura, devuelve las existentes sin
  descontar (el índice único por factura × producto de 004 se eliminó: con
  almacén y lote por línea una factura puede sacar el mismo producto en
  varios movimientos). Procesa primero las líneas con lote explícito.

El **vendedor** del prototipo de factura no es de inventario: queda para el
módulo de ventas.

### IR-2

`erp-api` toma el inventario final del IR-2 como
`sum(GREATEST(stock_actual, 0) * costo_promedio)` de `inventario.productos`.
Sigue siendo correcto: `stock_actual` es el total de todos los almacenes y
`costo_promedio` el promedio global; los servicios tienen existencia cero.
`GET /valuacion.valor_total` es exactamente ese valor.

## Configuración

| Variable | Por defecto | Uso |
|---|---|---|
| `DATABASE_URL` | — | obligatoria. |
| `DIAS_ALERTA_VENCIMIENTO` | `30` | días para "por vencer". |

## Pruebas

`pytest services/inventario_api` corre las pruebas unitarias. Las de
`tests/test_postgres.py` (migraciones, existencias, lotes/FEFO, ajustes,
transferencias, concurrencia, salida por factura, Kardex) necesitan un
Postgres real:

```bash
INVENTARIO_TEST_DATABASE_URL=postgresql://usuario@127.0.0.1:5432/postgres pytest services/inventario_api
```

Cada prueba crea y borra su propia base (el usuario necesita `CREATEDB`).
