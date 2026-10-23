# erp-api: ventas (contactos, cotizaciones, cobros)

`services/erp_api` es el backend de las pantallas de **Contactos**,
**Cotizaciones**, **Cobros / cuentas por cobrar** y **Notas** del frontend.
El contrato (rutas, campos y tipos) es
[`frontend/src/lib/erpApi.ts`](../frontend/src/lib/erpApi.ts); el backend lo
implementa 1:1. Montos viajan como string con 2 decimales (`"1250.00"`),
fechas como `YYYY-MM-DD` y marcas de tiempo en ISO 8601.

## Base de datos

- Usa su propio esquema **`ventas`** (`clientes`, `cotizaciones`, `cobros` y
  la secuencia `cotizaciones_numero_seq` del correlativo `COT-0001`).
- **Lo crea por sí mismo al arrancar**: después de conectarse aplica el DDL
  de [`services/erp_api/app/migraciones.py`](../services/erp_api/app/migraciones.py)
  (todo `IF NOT EXISTS`, idempotente, serializado con un advisory lock). En
  Supabase no hace falta correr nada a mano.
  [`db/init/007_ventas.sql`](../db/init/007_ventas.sql) es el mismo DDL, para
  el Postgres local de desarrollo (un test verifica que no se separen).
- **Solo lee** `facturacion.facturas` (de `facturas-api`) -- nunca la
  modifica. De `datos` (columna `json`) toma TipoPago, FechaEmision,
  FechaLimitePago, comprador, MontoTotal e InformacionReferencia.

## Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /clientes?q=&tipo=&activo=` | Lista contactos. `q` busca en nombre y RNC; `tipo=cliente`/`proveedor` incluye también a los `ambos` |
| `GET /clientes/{id}` · `GET /clientes/por-rnc/{rnc}` | Un contacto (404 si no existe) |
| `POST /clientes` · `PUT /clientes/{id}` | Crea / reemplaza. El RNC se guarda solo con dígitos: 9 para `rnc`, 11 para `cedula` (422 si no); RNC repetido → 409 |
| `DELETE /clientes/{id}` | 409 si tiene cotizaciones (desactívelo con `activo=false`) |
| `GET /cotizaciones?q=&estado=` · `GET /cotizaciones/{id}` | `estado=vencida` = abiertas con `vence` pasado (no se guarda, se calcula) |
| `POST /cotizaciones` · `PUT /cotizaciones/{id}` | El backend calcula importe por línea, subtotal, descuentos, ITBIS (18 % y 16 %; 0 y exento no suman) y total. `vence` por defecto = fecha + 15 días. PUT solo si está abierta (409) |
| `POST /cotizaciones/{id}/estado` | `abierta` \| `aceptada` \| `rechazada`; 409 si ya está facturada |
| `POST /cotizaciones/{id}/facturada` | La marca facturada y guarda `factura_id` |
| `DELETE /cotizaciones/{id}` | Solo abiertas (409) |
| `GET /cuentas-por-cobrar?estado=&cliente_rnc=` | Facturas enviadas/aceptadas a crédito (TipoPago 2) con cobrado, pendiente y estado (`pendiente`/`vencida`/`cobrada`). Sin `estado`: lo no cobrado; `estado=todas`: todo. Sin FechaLimitePago, vence a los 30 días de la emisión |
| `GET /cobros?factura_id=` · `POST /cobros` | Registra un cobro: 422 si la factura no es a crédito/no fue enviada o si el monto es ≤ 0 o supera lo pendiente. Fecha por defecto = hoy |
| `POST /cobros/{id}/anular` | Anula el cobro (deja de contar como cobrado); 409 si ya estaba anulado |
| `GET /documentos-venta?tipo_ecf=&estado=&q=` | Cualquier factura con cliente, fecha de emisión, total y, en notas de crédito/débito, el e-NCF afectado y el código de modificación. Más recientes primero |

"Hoy" se calcula en hora de República Dominicana (UTC-4), no en la del
servidor de la base de datos.

## Despliegue

Servicio `erp-api` en `docker-compose.dokploy.yml` (puerto de host 8019,
redes `facturacion` + `supabase`, mismo `DATABASE_URL` que los demás). El
frontend lo llama por el proxy de nginx `/api-erp/` (`VITE_ERP_API_URL`);
igual que `inventario-api`, no necesita Domain propio. Ver
[`DOKPLOY.md`](DOKPLOY.md).
