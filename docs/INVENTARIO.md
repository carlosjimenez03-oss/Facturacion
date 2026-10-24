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
    Se ejecuta **una sola vez** y queda anotado como versión `11` en
    `inventario.schema_version` (lo anota su última sentencia, así que el
    Postgres local que corre `db/init` también queda al día). Los arranques
    siguientes solo leen esa tabla: no corren ningún `ALTER TABLE` (cada
    uno toma un AccessExclusiveLock sobre `productos`/`movimientos` aunque
    no cambie nada, y frenaba o dejaba en deadlock las ventas en curso).
    El DDL sigue siendo idempotente.
  - La vez que sí migra: `lock_timeout` de 5 s y, de entrada, `LOCK TABLE
    productos, movimientos, compras, compra_items` esperando cada tabla
    menos que `deadlock_timeout` (800 ms con el valor por defecto de 1 s):
    si se cruza con una operación en curso es la migración la que se rinde
    (nunca la venta), suelta todo y reintenta con espera creciente (0,5 s a
    15 s, 8 intentos); si aun así no puede, el arranque falla y Docker lo
    reinicia. La migración reescribe `movimientos` (columna identidad
    `secuencia`): mientras corre, las operaciones de inventario esperan.
  - `tests/test_migraciones.py` verifica que el DDL de Python y los
    archivos de `db/init` no se separen y que cada migración anote su
    versión.
- Al migrar una base existente: se crea el almacén **Principal**, toda la
  existencia actual (`productos.stock_actual`) queda en él y los
  movimientos viejos se marcan con ese almacén. Eso ocurre solo en la
  migración. Si después `stock_actual` deja de coincidir con la suma por
  almacén (p. ej. lo escribió una versión anterior durante un despliegue
  que se solapó), **no se corrige solo**: cada arranque lo reporta en el
  log (`WARNING ... stock_actual distinto de la suma por almacén`, con los
  SKU). Ver "Despliegue".

### Despliegue (versión anterior a Fase 4)

La Fase 4 elimina el índice único `movimientos_salida_factura_idx`
(factura × producto) porque una factura puede sacar el mismo producto en
varios movimientos (dos líneas, varios lotes por FEFO, varios almacenes).
El `inventario-api` anterior registra la salida por factura con `ON
CONFLICT (referencia_id, producto_id) WHERE ...`, que **exige** ese índice:
no hay ningún índice que esa sentencia acepte y que a la vez permita varios
movimientos por factura × producto. Por eso:

- **La versión anterior y la nueva no deben atender a la vez** después de
  que la nueva migró. Con el Compose de Dokploy (una réplica, `docker
  compose up -d` detiene el contenedor viejo y arranca el nuevo) no hay
  solape. No usar un despliegue "rolling"/"start-first" para esta versión.
- Si aun así una réplica vieja atendió después de la migración: cada salida
  por factura que procesó **falló** (facturas-api la deja con
  `facturacion.facturas.inventario_sincronizado = false` e
  `inventario_error` con `no unique or exclusion constraint matching the ON
  CONFLICT specification`). facturas-api las reintenta solo: su tarea de
  fondo (cada `SINCRONIZAR_ESTADO_CADA_SEGUNDOS`) reenvía a
  `POST /movimientos/salida-por-factura` (idempotente por factura) toda
  factura enviada/aceptada con `inventario_sincronizado = false`. Lo que la versión vieja sí haya escrito (compras,
  productos) queda en `stock_actual` sin reparto por almacén y aparece en
  el aviso del log; se corrige asignando la diferencia al principal:

  ```sql
  INSERT INTO inventario.existencias (producto_id, almacen_id, cantidad)
  SELECT p.id, a.id, p.stock_actual - COALESCE(e.total, 0)
  FROM inventario.productos p
  CROSS JOIN inventario.almacenes a
  LEFT JOIN (SELECT producto_id, sum(cantidad) AS total FROM inventario.existencias GROUP BY producto_id) e
      ON e.producto_id = p.id
  WHERE a.es_principal AND p.stock_actual <> COALESCE(e.total, 0)
  ON CONFLICT (producto_id, almacen_id)
  DO UPDATE SET cantidad = inventario.existencias.cantidad + EXCLUDED.cantidad, actualizado_en = now();
  ```
- **Volver a la versión anterior** después de migrar exige recrear el
  índice (si no, todas sus salidas por factura fallan). Solo se puede si
  ninguna factura tiene dos salidas del mismo producto:

  ```sql
  SELECT referencia_id, producto_id, count(*) FROM inventario.movimientos
  WHERE referencia_tipo = 'factura' AND tipo = 'salida'
  GROUP BY 1, 2 HAVING count(*) > 1;   -- debe venir vacío

  CREATE UNIQUE INDEX CONCURRENTLY movimientos_salida_factura_idx
      ON inventario.movimientos (referencia_id, producto_id)
      WHERE referencia_tipo = 'factura' AND tipo = 'salida';
  ```

  Al volver a desplegar la Fase 4 no se elimina de nuevo (la migración ya
  está anotada): bórrelo a mano con `DROP INDEX CONCURRENTLY
  inventario.movimientos_salida_factura_idx` antes de que una factura
  saque el mismo producto dos veces.

### Modelo

| Tabla | Qué guarda |
|---|---|
| `productos` | + `tipo` (`bien`/`servicio`), `tasa_itbis` (`18`/`16`/`0`/`exento`), `codigo_barras` (único; vacío se guarda como NULL), `categoria`, `stock_minimo`, `maneja_lotes`. `stock_actual` sigue siendo el **total** de todos los almacenes y `costo_promedio` el promedio ponderado global. |
| `almacenes` | nombre (único sin importar mayúsculas), dirección, `es_principal` (exactamente uno), `permite_negativo`, `activo`. |
| `existencias` | cantidad por producto × almacén. Siempre suma `stock_actual`. |
| `lotes` | número (único por producto), `fecha_vencimiento`, `bloqueado`, notas. |
| `existencias_lote` | cantidad por lote × almacén. Lo que la existencia del almacén tiene de más es mercancía "sin lote". |
| `movimientos` | + `almacen_id`, `lote_id`, `referencia_linea` (NumeroLinea de la factura), `documento` (e-NCF, NCF, AJ-0001, TR-0001) y `secuencia` (orden real de aplicación: se asigna con el producto ya bloqueado). `creado_en` es `clock_timestamp()` (hora del INSERT, no del inicio de la transacción). `referencia_tipo`: `compra`, `factura`, `ajuste`, `anulacion_ajuste`, `transferencia`, `lote`. |
| `compras` / `compra_items` | + `almacen_id` de la compra; `lote_numero` y `lote_vencimiento` por línea. |
| `ajustes` / `ajuste_items` | correlativo `AJ-0001`, almacén, motivo, estado; líneas entrada/salida con costo (entradas) y lote. |
| `transferencias` / `transferencia_items` | correlativo `TR-0001`, origen, destino; líneas con lote. |
| `schema_version` | migraciones aplicadas (`11` = Fase 4). |

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
  movimiento. **Anular una entrada** (ajuste) la saca al costo con que
  entró y deshace el promedio: `(existencia·promedio − cantidad·costo) /
  (existencia − cantidad)`, o el costo de la entrada si no queda
  existencia positiva (10 a 100 + 10 a 200 = 20 a 150; anular la segunda
  deja 10 a 100).
- **Concurrencia**: cada movimiento bloquea la fila del producto
  (`FOR NO KEY UPDATE`), así que dos salidas simultáneas no sobrevenden;
  las operaciones de varias líneas bloquean sus productos en orden de id
  (sin deadlocks).
- **Existencia negativa**: transferencias y ajustes de salida la rechazan
  (409 con `faltante`) salvo en un almacén con `permite_negativo`. Una
  salida sin lote y sin FEFO (producto que no maneja lotes, o la anulación
  de una entrada sin lote) se valida contra lo **"sin lote"** (existencia
  del almacén − sus lotes), no contra el total: no puede tomar unidades de
  un lote. La
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
  bloqueado (ajuste por vencimiento). Registrar un lote, o cualquier
  entrada a un lote (compra con lote...), activa `maneja_lotes` del
  producto; `PUT /productos/{id}` con `maneja_lotes: false` da 409 mientras
  algún lote tenga existencia.
- **Almacenes inactivos**: no reciben ni entregan mercancía (transferencias,
  compras, ajustes, lotes: 409) y `validar-disponibilidad` reporta la venta
  desde uno como faltante (`motivo: "almacen_inactivo"`). La salida por
  factura sí sale de él (el e-CF ya se envió). Inactivar bloquea la fila del
  almacén y las operaciones la bloquean FOR SHARE: una transferencia en
  curso hacia el almacén termina antes y su existencia impide inactivarlo.

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
| `GET /valuacion` | valor total, por almacén y por categoría; contadores `sin_existencia` / `bajo_minimo`. Ver "Valuación con existencia negativa". |
| `POST /movimientos/validar-disponibilidad`, `POST /movimientos/salida-por-factura`, `POST /movimientos/reverso-por-factura` | uso interno de facturas-api (ver abajo). |

Errores: 404 no existe (también un almacén o lote referenciado en el
cuerpo), 409 la operación no aplica (estado, almacén inactivo, duplicado,
lote de otro producto, sin existencia — este último con `faltante:
{producto_id, nombre, almacen_id, lote_id, solicitado, disponible}`), 422
datos inválidos o producto inexistente en el cuerpo. Excepción:
`POST /movimientos/validar-disponibilidad` responde **422** (no 404) si un
almacén o lote del cuerpo no existe, y 409 si el lote es de otro producto.

### Valuación con existencia negativa

Igual que el IR-2, una existencia negativa no resta valor, pero se aplica
en dos niveles distintos: `valor_total` y `por_categoria[].valor` usan el
total del producto (`GREATEST(stock_actual, 0) × costo_promedio`), y
`por_almacen[].valor` (también el `valor` de `GET /almacenes`, `GET
/almacenes/{id}/existencias` y `GET /productos/{id}/existencias`) usa la
existencia de cada almacén (`GREATEST(cantidad, 0) × costo_promedio`). Sin
negativos coinciden; con un almacén en negativo la suma por almacén es
**mayor** que `valor_total` (10 en Principal y −4 en Sucursal a 2: 20 + 0
por almacén, 12 en total). `valor_total` es el que vale para el IR-2.

### Kardex

`GET /kardex` re-ejecuta todos los movimientos del producto (o del producto
en un almacén) y devuelve `saldo_inicial` (todo lo anterior a `desde`),
`filas` en orden cronológico — `concepto` ("Compra", "Venta", "Ajuste de
salida", "Transferencia enviada"...), `documento`, `cantidad` y
`costo_total` con signo, `costo_unitario`, y el acumulado
`saldo_cantidad`/`costo_promedio`/`saldo_valor` —, `saldo_final` y las
cantidades `entradas`/`salidas` del período. `tipo`/`referencia_tipo` solo
filtran filas; los saldos cuentan todo.

- Orden: `secuencia` (el orden en que se aplicaron), no `creado_en`.
- `promedio` usa el costo grabado en cada movimiento (las salidas al
  promedio vigente). Una entrada con existencia previa cero o negativa
  revalúa el saldo al costo de esa entrada (misma regla que el servicio),
  así que `saldo_final` = `stock_actual × costo_promedio`. `peps` es solo
  una vista: las salidas consumen las capas de entrada más antiguas.
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
  reporta un lote vencido o bloqueado, o un almacén inactivo, como faltante
  (`motivo` `lote_vencido`/`lote_bloqueado`/`almacen_inactivo`). Lo pedido
  con lote explícito no cuenta para el FEFO de otra línea. 422 si un
  almacén o lote no existe, 409 si el lote es de otro producto.
- `salida-por-factura` es **idempotente por factura**: un advisory lock por
  factura y, si ya hay salidas de esa factura, devuelve las existentes sin
  descontar (el índice único por factura × producto de 004 se eliminó: con
  almacén y lote por línea una factura puede sacar el mismo producto en
  varios movimientos). Procesa primero las líneas con lote explícito. Si
  falla (inventario-api caído, o una réplica vieja durante un despliegue
  solapado), facturas-api deja la factura con `inventario_sincronizado =
  false`; reenviar la misma factura es seguro. facturas-api la llama tras
  el envío exitoso a la DGII o, si la factura se **emite en contingencia**,
  al emitirla (la venta ya se entregó al comprador); el envío posterior en
  lote no la repite.
- `reverso-por-factura` (`{factura_id, documento}`) devuelve la mercancía
  de una factura que la **DGII rechazó** (estado definitivo `rechazado`:
  esa venta se vuelve a facturar con otro e-NCF): cada salida de la factura
  vuelve a su almacén y lote como entrada al costo con que salió
  (`referencia_tipo = 'reverso_factura'`, Kardex "Reverso de venta (e-CF
  rechazado)"), igual que la anulación de un ajuste. Es idempotente con el
  mismo advisory lock por factura que la salida: si ya hay un reverso
  devuelve sus movimientos; si la factura no tiene salidas no hace nada.
  Después de un reverso, `salida-por-factura` de esa factura sigue
  devolviendo las salidas existentes sin descontar. Si inventario-api no
  responde, facturas-api deja `inventario_reverso_pendiente = true` y lo
  reintenta su tarea de fondo. El reporte de productos más vendidos excluye
  las facturas revertidas.

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
