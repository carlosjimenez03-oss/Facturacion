"""Acceso a datos de Soluciones (fase 6) -- esquema `soluciones` (ver
`migraciones.DDL_SOLUCIONES`):

* Planes y suscripción: la prueba gratis (15 días desde el primer uso), las
  solicitudes de plan y el consumo del mes, calculado con datos reales
  (e-CF enviados a la DGII este mes, usuarios de Supabase Auth -- los cuenta
  la ruta con gotrue_admin.py, aquí solo llegan -- y el tamaño de los XML
  guardados).
* Portal de clientes: enlaces con token (solo se guarda su hash), revocables
  y con vencimiento, y la vista pública de solo lectura del cliente.
* Kontara Ventas: activación del módulo, las cuatro automatizaciones y la
  generación de su cola de envíos a partir de las facturas, cuentas por
  cobrar y contactos.
* POS: sesiones de caja y sus ventas (cada una con su e-CF en
  `facturacion.facturas`, que este servicio solo LEE).
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from libs.ecf_common.empresa import EMPRESA_PREDETERMINADA, empresa_actual, todas_las_empresas

from . import soluciones_calculos as calc
from .repository import (
    _CTE_CXC,
    _FECHA_EMISION,
    _NOMBRE_COMPRADOR,
    _RNC_COMPRADOR,
    _SELECT_CXC_CON_ESTADO,
    _TIPOS_CXC,
    _ZONA_RD,
    DatosInvalidosError,
    _con_id_str,
    _monto_dgii,
    hoy,
)

logger = logging.getLogger(__name__)


class NoEncontradoError(Exception):
    """El registro no existe (o el enlace del portal no es válido)."""


class ConflictoSolucionesError(Exception):
    """La operación no aplica al estado actual (p. ej. caja ya abierta)."""


def ahora() -> datetime:
    return datetime.now(_ZONA_RD)


_TOTAL = _monto_dgii("{Encabezado,Totales,MontoTotal}")


async def _razon_social_empresa(conn: asyncpg.Connection, configurada: str = "") -> str:
    """Nombre de la empresa para mensajes y portal: el configurado o el
    emisor de la última factura. Las empresas nuevas (multiempresa) usan el
    de su registro (EMISOR_RAZON_SOCIAL es el de la empresa 1)."""
    if empresa_actual() != EMPRESA_PREDETERMINADA:
        nombre = await conn.fetchval(
            "SELECT COALESCE(NULLIF(nombre_comercial, ''), razon_social) FROM configuracion.empresas WHERE id = $1",
            empresa_actual(),
        )
        if nombre:
            return nombre
    if configurada:
        return configurada
    nombre = await conn.fetchval(
        "SELECT NULLIF(datos::jsonb #>> '{Encabezado,Emisor,RazonSocialEmisor}', '')"
        " FROM facturacion.facturas ORDER BY creado_en DESC LIMIT 1"
    )
    return nombre or "Nuestra empresa"


# --- Planes y suscripción ---------------------------------------------------------

async def obtener_suscripcion(pool: asyncpg.Pool) -> dict[str, Any]:
    """La primera consulta inicia la prueba gratis (primer uso)."""
    dia = hoy()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soluciones.suscripcion (id, prueba_inicio, prueba_fin)
            VALUES (1, $1, $2)
            ON CONFLICT (id) DO NOTHING
            """,
            dia,
            dia + timedelta(days=calc.DIAS_PRUEBA - 1),
        )
        fila = await conn.fetchrow("SELECT * FROM soluciones.suscripcion WHERE id = 1")
        solicitud = await conn.fetchrow(
            "SELECT * FROM soluciones.solicitudes_plan WHERE estado = 'pendiente' ORDER BY creado_en DESC LIMIT 1"
        )
    resultado = calc.estado_suscripcion(dict(fila), dia)
    resultado["solicitud_pendiente"] = _con_id_str(solicitud) if solicitud else None
    return resultado


async def consumo(
    pool: asyncpg.Pool, usuarios: int | None = None, empresas: dict[int, str] | None = None
) -> dict[str, Any]:
    """Uso real del mes: e-CF enviados a la DGII (con trackId) con fecha de
    emisión este mes, usuarios y bytes de XML guardados (emitidos y
    recibidos). `usuarios` = usuarios activos de Supabase Auth (los cuenta
    soluciones.py con la API admin de GoTrue); None = no se pueden contar
    (sin SUPABASE_SERVICE_ROLE_KEY) y la pantalla muestra "—".

    Multiempresa: el plan es de la instalación, así que se cuenta lo de
    TODAS las empresas (el total contra el límite) y se desglosa por
    empresa solo para las de `empresas` ({id: nombre}, las del usuario)."""
    dia = hoy()
    inicio = dia.replace(day=1)
    fin = (inicio + timedelta(days=32)).replace(day=1)
    with todas_las_empresas():
        async with pool.acquire() as conn:
            suscripcion = await conn.fetchrow("SELECT plan FROM soluciones.suscripcion WHERE id = 1")
            por_empresa = {
                fila["empresa_id"]: fila["n"]
                for fila in await conn.fetch(
                    f"""
                    SELECT f.empresa_id, count(*) AS n
                    FROM facturacion.facturas f
                    CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
                    WHERE f.track_id IS NOT NULL
                      AND COALESCE({_FECHA_EMISION}, f.creado_en::date) >= $1
                      AND COALESCE({_FECHA_EMISION}, f.creado_en::date) < $2
                    GROUP BY f.empresa_id
                    """,
                    inicio,
                    fin,
                )
            }
            bytes_emitidos = await conn.fetchval(
                "SELECT COALESCE(sum(COALESCE(octet_length(xml_generado), 0) + COALESCE(octet_length(xml_firmado), 0)), 0)"
                " FROM facturacion.facturas"
            )
            bytes_recibidos = 0
            if await conn.fetchval("SELECT to_regclass('recepcion.ecf_recibidos')") is not None:
                bytes_recibidos = await conn.fetchval(
                    "SELECT COALESCE(sum(octet_length(xml) + COALESCE(octet_length(acecf_xml), 0)), 0)"
                    " FROM recepcion.ecf_recibidos"
                )
    emitidos = sum(por_empresa.values())
    plan = (suscripcion["plan"] if suscripcion else None) or calc.PLAN_DE_PRUEBA
    limites = calc.PLANES[plan]
    almacenamiento = int(bytes_emitidos) + int(bytes_recibidos)
    return {
        "mes": inicio,
        "plan_limites": plan,
        "ecf_emitidos": emitidos,
        "ecf_por_empresa": [
            {"empresa_id": empresa_id, "nombre": nombre, "ecf_emitidos": por_empresa.get(empresa_id, 0)}
            for empresa_id, nombre in (empresas or {}).items()
        ],
        "ecf_limite": limites["ecf_mes"],
        "ecf_porcentaje": calc.porcentaje_uso(emitidos, limites["ecf_mes"]),
        "usuarios": usuarios,
        "usuarios_limite": limites["usuarios"],
        "usuarios_porcentaje": calc.porcentaje_uso(usuarios, limites["usuarios"]),
        "almacenamiento_bytes": almacenamiento,
        "almacenamiento_limite_bytes": limites["almacenamiento_bytes"],
        "almacenamiento_texto": calc.formato_bytes(almacenamiento),
        "almacenamiento_limite_texto": calc.formato_bytes(limites["almacenamiento_bytes"]),
        "almacenamiento_porcentaje": calc.porcentaje_uso(almacenamiento, limites["almacenamiento_bytes"]),
    }


async def crear_solicitud_plan(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    montos = calc.precio_plan(campos["plan"], campos["periodicidad"])
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO soluciones.solicitudes_plan
                (plan, periodicidad, forma_pago, subtotal, itbis, total,
                 contacto_nombre, contacto_correo, contacto_telefono, notas)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *
            """,
            campos["plan"],
            campos["periodicidad"],
            campos["forma_pago"],
            montos["subtotal"],
            montos["itbis"],
            montos["total"],
            campos.get("contacto_nombre"),
            campos.get("contacto_correo"),
            campos.get("contacto_telefono"),
            campos.get("notas"),
        )
    return _con_id_str(fila)


async def listar_solicitudes_plan(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch("SELECT * FROM soluciones.solicitudes_plan ORDER BY creado_en DESC LIMIT 50")
    return [_con_id_str(f) for f in filas]


# --- Portal de clientes ---------------------------------------------------------

_SELECT_ENLACES = """
    SELECT e.id, e.cliente_id, c.nombre AS cliente_nombre, COALESCE(e.rnc, c.rnc) AS cliente_rnc,
           c.correo AS cliente_correo, c.telefono AS cliente_telefono,
           e.metodos_pago, e.expira_en, e.revocado_en, e.ultimo_acceso_en, e.accesos, e.creado_en,
           CASE WHEN e.revocado_en IS NOT NULL THEN 'revocado'
                WHEN e.expira_en <= now() THEN 'vencido'
                ELSE 'activo' END AS estado
    FROM soluciones.portal_enlaces e
    JOIN ventas.clientes c ON c.id = e.cliente_id
"""


def _fila_enlace(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    datos["cliente_id"] = str(datos["cliente_id"])
    datos["metodos_pago"] = list(datos["metodos_pago"])
    return datos


async def crear_enlace(
    pool: asyncpg.Pool, *, cliente_id: uuid.UUID, metodos_pago: list[str], dias_validez: int
) -> dict[str, Any]:
    """Devuelve el enlace con su `token` en claro: es la única vez que se ve."""
    metodos = list(dict.fromkeys(metodos_pago))
    for metodo in metodos:
        if metodo not in calc.METODOS_PAGO_PORTAL:
            raise DatosInvalidosError(f"Método de pago desconocido: {metodo}")
    token, token_hash = calc.generar_token()
    async with pool.acquire() as conn:
        cliente = await conn.fetchrow("SELECT id, rnc, activo FROM ventas.clientes WHERE id = $1", cliente_id)
        if cliente is None:
            raise NoEncontradoError("Cliente no encontrado")
        if not cliente["rnc"]:
            raise DatosInvalidosError(
                "El cliente necesita RNC o cédula: el portal muestra las facturas emitidas a esa identificación"
            )
        nuevo_id = await conn.fetchval(
            """
            INSERT INTO soluciones.portal_enlaces (cliente_id, token_hash, metodos_pago, expira_en, rnc)
            VALUES ($1, $2, $3, now() + make_interval(days => $4), $5)
            RETURNING id
            """,
            cliente_id,
            token_hash,
            metodos,
            dias_validez,
            cliente["rnc"],
        )
        fila = await conn.fetchrow(f"{_SELECT_ENLACES} WHERE e.id = $1", nuevo_id)
    return _fila_enlace(fila) | {"token": token}


async def listar_enlaces(pool: asyncpg.Pool, *, cliente_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        if cliente_id:
            filas = await conn.fetch(f"{_SELECT_ENLACES} WHERE e.cliente_id = $1 ORDER BY e.creado_en DESC", cliente_id)
        else:
            filas = await conn.fetch(f"{_SELECT_ENLACES} ORDER BY e.creado_en DESC LIMIT 200")
    return [_fila_enlace(f) for f in filas]


async def revocar_enlace(pool: asyncpg.Pool, enlace_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        actualizado = await conn.fetchval(
            "UPDATE soluciones.portal_enlaces SET revocado_en = COALESCE(revocado_en, now()) WHERE id = $1 RETURNING id",
            enlace_id,
        )
        if actualizado is None:
            raise NoEncontradoError("Enlace no encontrado")
        fila = await conn.fetchrow(f"{_SELECT_ENLACES} WHERE e.id = $1", enlace_id)
    return _fila_enlace(fila)


_CAMPOS_CONFIG = ("banco", "tipo_cuenta", "numero_cuenta", "titular", "rnc_titular", "instrucciones")


async def obtener_config_portal(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow("SELECT * FROM soluciones.portal_config WHERE id = 1")
    if fila is None:
        return {campo: None for campo in _CAMPOS_CONFIG} | {"actualizado_en": None}
    return {campo: fila[campo] for campo in (*_CAMPOS_CONFIG, "actualizado_en")}


async def guardar_config_portal(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    valores = [campos.get(c) or None for c in _CAMPOS_CONFIG]
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soluciones.portal_config (id, banco, tipo_cuenta, numero_cuenta, titular, rnc_titular, instrucciones)
            VALUES (1, $1, $2, $3, $4, $5, $6)
            ON CONFLICT (empresa_id, id) DO UPDATE SET
                banco = EXCLUDED.banco, tipo_cuenta = EXCLUDED.tipo_cuenta,
                numero_cuenta = EXCLUDED.numero_cuenta, titular = EXCLUDED.titular,
                rnc_titular = EXCLUDED.rnc_titular, instrucciones = EXCLUDED.instrucciones,
                actualizado_en = now()
            """,
            *valores,
        )
    return await obtener_config_portal(pool)


async def _enlace_vigente(conn: asyncpg.Connection, token: str) -> asyncpg.Record:
    if not calc.token_con_forma_valida(token):
        raise NoEncontradoError("Enlace no válido")
    fila = await conn.fetchrow(
        """
        SELECT e.id, e.metodos_pago, e.expira_en, c.id AS cliente_id, c.nombre, COALESCE(e.rnc, c.rnc) AS rnc
        FROM soluciones.portal_enlaces e
        JOIN ventas.clientes c ON c.id = e.cliente_id
        WHERE e.token_hash = $1 AND e.revocado_en IS NULL AND e.expira_en > now()
        """,
        calc.hash_token(token),
    )
    if fila is None or not fila["rnc"]:
        raise NoEncontradoError("Enlace no válido, vencido o revocado")
    return fila


# Tipos de e-CF que ve el cliente en su portal (los emitidos a él).
_TIPOS_PORTAL = "(31, 32, 33, 34, 44, 45, 46)"

_SELECT_FACTURAS_PORTAL = f"""
    WITH {_CTE_CXC}
    SELECT f.id, f.encf, f.tipo_ecf,
           COALESCE({_FECHA_EMISION}, f.creado_en::date) AS fecha_emision,
           {_TOTAL} AS total,
           cxc.pendiente, cxc.vence,
           (f.xml_firmado IS NOT NULL) AS firmada
    FROM facturacion.facturas f
    CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
    LEFT JOIN cxc ON cxc.factura_id = f.id
    WHERE f.estado = 'aceptado' AND f.tipo_ecf IN {_TIPOS_PORTAL} AND {_RNC_COMPRADOR} = $1
"""


def _estado_pago(fila: dict[str, Any], dia: date) -> str:
    if fila["tipo_ecf"] == 34:
        return "nota_credito"
    if fila["pendiente"] is None:
        return "contado"
    if fila["pendiente"] <= 0:
        return "pagada"
    return "vencida" if fila["vence"] < dia else "pendiente"


async def portal_publico(
    pool: asyncpg.Pool, token: str, *, empresa_configurada: str = "", registrar_acceso: bool = True
) -> dict[str, Any]:
    """Lo que ve el cliente con su enlace: solo SUS e-CF aceptados por la
    DGII (por su RNC/cédula), su saldo pendiente y cómo pagar. La vista
    previa del usuario (`registrar_acceso=False`) no cuenta como acceso."""
    dia = hoy()
    async with pool.acquire() as conn:
        enlace = await _enlace_vigente(conn, token)
        if registrar_acceso:
            await conn.execute(
                "UPDATE soluciones.portal_enlaces SET accesos = accesos + 1, ultimo_acceso_en = now() WHERE id = $1",
                enlace["id"],
            )
        filas = await conn.fetch(f"{_SELECT_FACTURAS_PORTAL} ORDER BY 4 DESC, f.encf DESC", enlace["rnc"])
        config = await conn.fetchrow("SELECT * FROM soluciones.portal_config WHERE id = 1")
        empresa = await _razon_social_empresa(conn, empresa_configurada)
    facturas = []
    saldo = Decimal("0")
    for fila in filas:
        datos = dict(fila)
        estado = _estado_pago(datos, dia)
        pendiente = datos["pendiente"] if estado in ("pendiente", "vencida") else Decimal("0")
        saldo += pendiente
        facturas.append(
            {
                "id": str(datos["id"]),
                "encf": datos["encf"],
                "tipo_ecf": datos["tipo_ecf"],
                "fecha_emision": datos["fecha_emision"],
                "total": datos["total"],
                "pendiente": pendiente,
                "vence": datos["vence"],
                "estado_pago": estado,
            }
        )
    metodos = list(enlace["metodos_pago"])
    transferencia = None
    if "transferencia" in metodos and config is not None and config["numero_cuenta"]:
        transferencia = {c: config[c] for c in _CAMPOS_CONFIG}
    return {
        "empresa": empresa,
        "cliente_nombre": enlace["nombre"],
        "cliente_rnc": enlace["rnc"],
        "expira_en": enlace["expira_en"],
        "metodos_pago": metodos,
        "transferencia": transferencia,
        "saldo_pendiente": calc.redondear(saldo),
        "facturas": facturas,
    }


async def factura_del_portal(pool: asyncpg.Pool, token: str, factura_id: uuid.UUID) -> dict[str, Any]:
    """La factura solo si el enlace es vigente Y es una de las del cliente."""
    async with pool.acquire() as conn:
        enlace = await _enlace_vigente(conn, token)
        fila = await conn.fetchrow(
            f"""
            SELECT f.id, f.encf, f.xml_firmado
            FROM facturacion.facturas f
            CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
            WHERE f.id = $1 AND f.estado = 'aceptado' AND f.tipo_ecf IN {_TIPOS_PORTAL} AND {_RNC_COMPRADOR} = $2
            """,
            factura_id,
            enlace["rnc"],
        )
    if fila is None:
        raise NoEncontradoError("Factura no encontrada")
    return {"id": str(fila["id"]), "encf": fila["encf"], "xml_firmado": fila["xml_firmado"]}


async def xml_firmado_factura(pool: asyncpg.Pool, factura_id: uuid.UUID) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT xml_firmado FROM facturacion.facturas WHERE id = $1", factura_id)


# --- Kontara Ventas ---------------------------------------------------------

async def _asegurar_automatizaciones(conn: asyncpg.Connection) -> None:
    for clave, a in calc.AUTOMATIZACIONES.items():
        await conn.execute(
            """
            INSERT INTO soluciones.automatizaciones (clave, dias, asunto, plantilla)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (empresa_id, clave) DO NOTHING
            """,
            clave,
            a["dias"],
            a["asunto"],
            a["plantilla"],
        )


def _fila_automatizacion(fila: asyncpg.Record, conteos: dict[str, dict[str, int]]) -> dict[str, Any]:
    base = calc.AUTOMATIZACIONES[fila["clave"]]
    return {
        "clave": fila["clave"],
        "categoria": base["categoria"],
        "titulo": base["titulo"],
        "descripcion": base["descripcion"],
        "variables": base["variables"],
        "activa": fila["activa"],
        "canal": fila["canal"],
        "dias": fila["dias"],
        "asunto": fila["asunto"],
        "plantilla": fila["plantilla"],
        "activada_en": fila["activada_en"],
        "envios": conteos.get(fila["clave"], {}),
    }


async def estado_ventas(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO soluciones.ventas_modulo (id) VALUES (1) ON CONFLICT (empresa_id, id) DO NOTHING")
        await _asegurar_automatizaciones(conn)
        modulo = await conn.fetchrow("SELECT * FROM soluciones.ventas_modulo WHERE id = 1")
        filas = await conn.fetch("SELECT * FROM soluciones.automatizaciones ORDER BY array_position(ARRAY['motivar','cobranza','fidelizacion','reactivacion'], clave)")
        conteo = await conn.fetch(
            "SELECT automatizacion, estado, count(*) AS n FROM soluciones.envios GROUP BY 1, 2"
        )
        plan = await conn.fetchval("SELECT plan FROM soluciones.suscripcion WHERE id = 1")
    conteos: dict[str, dict[str, int]] = {}
    for c in conteo:
        conteos.setdefault(c["automatizacion"], {})[c["estado"]] = c["n"]
    dia = hoy()
    en_prueba = bool(modulo["prueba_hasta"] and modulo["prueba_hasta"] >= dia)
    ultimo = modulo["ultimo_resultado"]
    return {
        "activo": modulo["activo"],
        "activado_en": modulo["activado_en"],
        "prueba_hasta": modulo["prueba_hasta"],
        "habilitado": modulo["activo"] and (en_prueba or plan in ("pyme", "empresarial")),
        "ultima_ejecucion": modulo["ultima_ejecucion"],
        "ultimo_resultado": json.loads(ultimo) if isinstance(ultimo, str) else ultimo,
        "automatizaciones": [_fila_automatizacion(f, conteos) for f in filas],
    }


async def activar_ventas(pool: asyncpg.Pool, activo: bool) -> dict[str, Any]:
    """La prueba de 30 días empieza la primera vez que se activa (desactivar
    y volver a activar no la reinicia)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO soluciones.ventas_modulo (id, activo, activado_en, prueba_hasta)
            VALUES (1, $1, CASE WHEN $1 THEN now() END, CASE WHEN $1 THEN $2::date END)
            ON CONFLICT (empresa_id, id) DO UPDATE SET
                activo = EXCLUDED.activo,
                activado_en = CASE WHEN EXCLUDED.activo THEN COALESCE(soluciones.ventas_modulo.activado_en, now())
                                   ELSE soluciones.ventas_modulo.activado_en END,
                prueba_hasta = CASE WHEN EXCLUDED.activo THEN COALESCE(soluciones.ventas_modulo.prueba_hasta, $2::date)
                                    ELSE soluciones.ventas_modulo.prueba_hasta END
            """,
            activo,
            hoy() + timedelta(days=calc.DIAS_PRUEBA_VENTAS),
        )
    return await estado_ventas(pool)


async def actualizar_automatizacion(pool: asyncpg.Pool, clave: str, **campos: Any) -> dict[str, Any]:
    if clave not in calc.AUTOMATIZACIONES:
        raise NoEncontradoError("Automatización no encontrada")
    cambios = {k: v for k, v in campos.items() if v is not None}
    if "plantilla" in cambios and not cambios["plantilla"].strip():
        raise DatosInvalidosError("La plantilla del mensaje no puede quedar vacía")
    async with pool.acquire() as conn:
        await _asegurar_automatizaciones(conn)
        asignaciones = []
        valores: list[Any] = [clave]
        for campo in ("activa", "canal", "dias", "asunto", "plantilla"):
            if campo in cambios:
                valores.append(cambios[campo])
                asignaciones.append(f"{campo} = ${len(valores)}")
                if campo == "activa":
                    # Desde cuándo cuenta (fidelización solo agradece las compras posteriores).
                    asignaciones.append(
                        f"activada_en = CASE WHEN ${len(valores)} AND NOT activa THEN now() ELSE activada_en END"
                    )
        if asignaciones:
            await conn.execute(
                f"UPDATE soluciones.automatizaciones SET {', '.join(asignaciones)}, actualizado_en = now() WHERE clave = $1",
                *valores,
            )
    estado = await estado_ventas(pool)
    return next(a for a in estado["automatizaciones"] if a["clave"] == clave)


async def _categorias_catalogo(conn: asyncpg.Connection) -> str:
    if await conn.fetchval("SELECT to_regclass('inventario.productos')") is None:
        return "nuestros productos y servicios"
    filas = await conn.fetch(
        "SELECT categoria FROM inventario.productos WHERE activo AND NULLIF(categoria, '') IS NOT NULL"
        " GROUP BY categoria ORDER BY count(*) DESC, categoria LIMIT 6"
    )
    return ", ".join(f["categoria"] for f in filas) or "nuestros productos y servicios"


_SELECT_ULTIMA_COMPRA = f"""
    WITH compras AS (
        SELECT {_RNC_COMPRADOR} AS rnc, max(COALESCE({_FECHA_EMISION}, f.creado_en::date)) AS ultima
        FROM facturacion.facturas f
        CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
        WHERE f.estado IN ('aceptado', 'enviado', 'contingencia') AND f.tipo_ecf IN {_TIPOS_CXC}
        GROUP BY 1
    )
    SELECT c.id, c.nombre, c.telefono, c.correo, co.ultima, ($1::date - co.ultima) AS dias_sin_comprar
    FROM ventas.clientes c
    JOIN compras co ON co.rnc = c.rnc
    WHERE c.activo AND c.tipo IN ('cliente', 'ambos')
"""


def _destinos(canal: str, telefono: str | None, correo: str | None) -> list[tuple[str, str]]:
    destinos = []
    for c in calc.canales_de(canal):
        if c == "whatsapp" and (tel := calc.telefono_whatsapp(telefono)):
            destinos.append((c, tel))
        elif c == "correo" and calc.correo_valido(correo):
            destinos.append((c, correo.strip()))
    return destinos


async def generar_envios(pool: asyncpg.Pool, *, empresa_configurada: str = "") -> dict[str, Any]:
    """Revisa las automatizaciones activas y encola los envíos que tocan hoy.
    Idempotente: `clave_unica` + canal evita duplicados, así que correrla
    varias veces al día (o en dos réplicas) no repite mensajes. Los clientes
    sin teléfono/correo para el canal se cuentan como omitidos."""
    estado = await estado_ventas(pool)
    resultado: dict[str, Any] = {"creados": 0, "omitidos_sin_contacto": 0, "por_automatizacion": {}}
    if not estado["habilitado"]:
        resultado["motivo"] = "El módulo Kontara Ventas no está activo (o terminó su prueba gratis)."
        return resultado
    autos = {a["clave"]: a for a in estado["automatizaciones"]}
    dia = hoy()
    mes = dia.strftime("%Y-%m")
    candidatos: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        empresa = await _razon_social_empresa(conn, empresa_configurada)
        dias_reactivacion = autos["reactivacion"]["dias"]

        if autos["motivar"]["activa"] or autos["reactivacion"]["activa"]:
            clientes = await conn.fetch(_SELECT_ULTIMA_COMPRA, dia)
            categorias = await _categorias_catalogo(conn) if autos["motivar"]["activa"] else ""
            for c in clientes:
                variables = {"cliente": c["nombre"], "empresa": empresa, "dias_sin_comprar": c["dias_sin_comprar"]}
                a = autos["motivar"]
                if a["activa"] and a["dias"] <= c["dias_sin_comprar"] <= dias_reactivacion:
                    candidatos.append(
                        {"auto": a, "clave": f"motivar:{c['id']}:{mes}", "cliente": c,
                         "variables": variables | {"categorias": categorias}}
                    )
                a = autos["reactivacion"]
                if a["activa"] and c["dias_sin_comprar"] > dias_reactivacion:
                    candidatos.append(
                        {"auto": a, "clave": f"reactivacion:{c['id']}:{c['ultima']}", "cliente": c, "variables": variables}
                    )

        a = autos["cobranza"]
        if a["activa"]:
            cuentas = await conn.fetch(
                f"""
                SELECT cta.*, cl.id AS cliente_id, cl.correo AS cliente_correo
                FROM ({_SELECT_CXC_CON_ESTADO}) cta
                LEFT JOIN ventas.clientes cl ON cl.rnc = cta.cliente_rnc
                WHERE cta.estado = 'pendiente' AND cta.vence BETWEEN $1::date AND $1::date + $2::int
                """,
                dia,
                a["dias"],
            )
            for cta in cuentas:
                candidatos.append(
                    {
                        "auto": a,
                        "clave": f"cobranza:{cta['factura_id']}:{cta['vence']}",
                        "cliente": {"id": cta["cliente_id"], "nombre": cta["cliente_nombre"],
                                    "telefono": cta["cliente_telefono"], "correo": cta["cliente_correo"]},
                        "factura_id": cta["factura_id"],
                        "encf": cta["encf"],
                        "variables": {"cliente": cta["cliente_nombre"], "empresa": empresa, "encf": cta["encf"],
                                      "monto": f"{cta['pendiente']:,.2f}", "vence": cta["vence"].strftime("%d/%m/%Y")},
                    }
                )

        a = autos["fidelizacion"]
        if a["activa"] and a["activada_en"]:
            facturas = await conn.fetch(
                f"""
                SELECT f.id, f.encf, {_TOTAL} AS total,
                       COALESCE(cl.nombre, {_NOMBRE_COMPRADOR}, 'Cliente') AS nombre,
                       cl.id AS cliente_id, cl.telefono,
                       COALESCE(cl.correo, NULLIF(d #>> '{{Encabezado,Comprador,CorreoComprador}}', '')) AS correo
                FROM facturacion.facturas f
                CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
                LEFT JOIN ventas.clientes cl ON cl.rnc = {_RNC_COMPRADOR}
                WHERE f.estado = 'aceptado' AND f.tipo_ecf IN {_TIPOS_CXC}
                  AND f.actualizado_en >= $1 AND f.actualizado_en >= now() - make_interval(days => $2)
                """,
                a["activada_en"],
                max(a["dias"], 1),
            )
            for f in facturas:
                candidatos.append(
                    {
                        "auto": a,
                        "clave": f"fidelizacion:{f['id']}",
                        "cliente": {"id": f["cliente_id"], "nombre": f["nombre"], "telefono": f["telefono"], "correo": f["correo"]},
                        "factura_id": f["id"],
                        "encf": f["encf"],
                        "variables": {"cliente": f["nombre"], "empresa": empresa, "encf": f["encf"], "total": f"{f['total']:,.2f}"},
                    }
                )

        for cand in candidatos:
            a = cand["auto"]
            cliente = cand["cliente"]
            destinos = _destinos(a["canal"], cliente.get("telefono"), cliente.get("correo"))
            if not destinos:
                resultado["omitidos_sin_contacto"] += 1
                continue
            mensaje = calc.renderizar_plantilla(a["plantilla"], cand["variables"])
            asunto = calc.renderizar_plantilla(a["asunto"], cand["variables"])
            for canal, destino in destinos:
                nuevo = await conn.fetchval(
                    """
                    INSERT INTO soluciones.envios
                        (automatizacion, clave_unica, canal, cliente_id, cliente_nombre, destino,
                         factura_id, encf, asunto, mensaje)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (empresa_id, clave_unica, canal) DO NOTHING
                    RETURNING id
                    """,
                    a["clave"],
                    cand["clave"],
                    canal,
                    cliente.get("id"),
                    cliente.get("nombre") or "Cliente",
                    destino,
                    cand.get("factura_id"),
                    cand.get("encf"),
                    asunto,
                    mensaje,
                )
                if nuevo is not None:
                    resultado["creados"] += 1
                    por = resultado["por_automatizacion"]
                    por[a["clave"]] = por.get(a["clave"], 0) + 1
        await conn.execute(
            "UPDATE soluciones.ventas_modulo SET ultima_ejecucion = now(), ultimo_resultado = $1::jsonb WHERE id = 1",
            json.dumps(resultado),
        )
    return resultado


_SELECT_ENVIOS = """
    SELECT id, automatizacion, canal, cliente_id, cliente_nombre, destino, factura_id, encf,
           asunto, mensaje, estado, error, intentos, enviado_en, creado_en
    FROM soluciones.envios
"""


def _fila_envio(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    for campo in ("cliente_id", "factura_id"):
        datos[campo] = str(datos[campo]) if datos[campo] else None
    return datos


async def listar_envios(
    pool: asyncpg.Pool, *, automatizacion: str | None = None, estado: str | None = None, limite: int = 200
) -> list[dict[str, Any]]:
    condiciones, valores = [], []
    if automatizacion:
        valores.append(automatizacion)
        condiciones.append(f"automatizacion = ${len(valores)}")
    if estado:
        valores.append(estado)
        condiciones.append(f"estado = ${len(valores)}")
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    valores.append(limite)
    async with pool.acquire() as conn:
        filas = await conn.fetch(f"{_SELECT_ENVIOS} {where} ORDER BY creado_en DESC LIMIT ${len(valores)}", *valores)
    return [_fila_envio(f) for f in filas]


async def marcar_envio(pool: asyncpg.Pool, envio_id: uuid.UUID, estado: str) -> dict[str, Any]:
    """El usuario lo envió a mano (wa.me / su correo) o lo descarta. Uno ya
    entregado por un canal no se toca."""
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(f"{_SELECT_ENVIOS} WHERE id = $1", envio_id)
        if fila is None:
            raise NoEncontradoError("Envío no encontrado")
        if fila["estado"] == "enviado":
            raise ConflictoSolucionesError("Este envío ya lo entregó el canal configurado")
        await conn.execute(
            """
            UPDATE soluciones.envios
            SET estado = $2, enviado_en = CASE WHEN $2 = 'enviado_manual' THEN now() ELSE enviado_en END
            WHERE id = $1
            """,
            envio_id,
            estado,
        )
        fila = await conn.fetchrow(f"{_SELECT_ENVIOS} WHERE id = $1", envio_id)
    return _fila_envio(fila)


_MAX_INTENTOS = 3


async def envios_por_entregar(pool: asyncpg.Pool, canales: list[str]) -> list[dict[str, Any]]:
    if not canales:
        return []
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"""{_SELECT_ENVIOS}
            WHERE canal = ANY($1::text[]) AND estado IN ('pendiente_canal', 'error') AND intentos < $2
            ORDER BY creado_en LIMIT 200""",
            canales,
            _MAX_INTENTOS,
        )
    return [_fila_envio(f) for f in filas]


async def registrar_entrega(pool: asyncpg.Pool, envio_id: str, *, error: str | None, definitivo: bool = False) -> None:
    """`definitivo`: el error no se arregla reintentando (p. ej. WhatsApp
    fuera de la ventana de 24 h sin plantilla): se agotan los intentos para
    que la tarea no lo vuelva a mandar en cada ciclo."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE soluciones.envios
            SET intentos = CASE WHEN $3 THEN greatest(intentos + 1, $4) ELSE intentos + 1 END,
                estado = CASE WHEN $2::text IS NULL THEN 'enviado' ELSE 'error' END,
                error = $2::text,
                enviado_en = CASE WHEN $2::text IS NULL THEN now() ELSE enviado_en END
            WHERE id = $1
            """,
            uuid.UUID(envio_id),
            error,
            definitivo,
            _MAX_INTENTOS,
        )


# --- POS ---------------------------------------------------------

def _decimales(d: dict[str, Any] | str | None) -> dict[str, Decimal] | None:
    if d is None:
        return None
    if isinstance(d, str):
        d = json.loads(d)
    return {k: Decimal(str(v)) for k, v in d.items()}


def _fila_venta(fila: asyncpg.Record) -> dict[str, Any]:
    datos = _con_id_str(fila)
    datos["sesion_id"] = str(datos["sesion_id"])
    datos["factura_id"] = str(datos["factura_id"])
    pagos = datos["pagos"]
    datos["pagos"] = json.loads(pagos) if isinstance(pagos, str) else pagos
    return datos


async def _sesion_completa(conn: asyncpg.Connection, sesion_id: uuid.UUID) -> dict[str, Any] | None:
    fila = await conn.fetchrow("SELECT * FROM soluciones.pos_sesiones WHERE id = $1", sesion_id)
    if fila is None:
        return None
    ventas = [
        _fila_venta(v)
        for v in await conn.fetch(
            "SELECT * FROM soluciones.pos_ventas WHERE sesion_id = $1 ORDER BY creado_en", sesion_id
        )
    ]
    datos = _con_id_str(fila)
    datos["almacen_id"] = str(datos["almacen_id"]) if datos["almacen_id"] else None
    esperado = _decimales(datos["esperado"]) or calc.esperado_en_caja(datos["fondo_inicial"], ventas)
    contado = _decimales(datos["contado"])
    datos["esperado"] = esperado
    datos["contado"] = contado
    datos["diferencias"] = calc.diferencias_arqueo(esperado, contado) if contado is not None else None
    datos["ventas"] = ventas
    datos["cantidad_ventas"] = len(ventas)
    datos["total_ventas"] = calc.redondear(sum((Decimal(v["total"]) for v in ventas), Decimal("0")))
    return datos


async def abrir_sesion(
    pool: asyncpg.Pool, *, caja: str, fondo_inicial: Decimal, cajero: str | None, almacen_id: uuid.UUID | None
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        try:
            nuevo = await conn.fetchval(
                """
                INSERT INTO soluciones.pos_sesiones (caja, fondo_inicial, cajero, almacen_id)
                VALUES ($1, $2, $3, $4) RETURNING id
                """,
                caja,
                calc.redondear(fondo_inicial),
                cajero,
                almacen_id,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictoSolucionesError(f"La {caja} ya tiene una sesión abierta; ciérrela primero") from exc
        return await _sesion_completa(conn, nuevo)


async def sesion_abierta(pool: asyncpg.Pool, caja: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        sid = await conn.fetchval(
            "SELECT id FROM soluciones.pos_sesiones WHERE caja = $1 AND estado = 'abierta'", caja
        )
        return await _sesion_completa(conn, sid) if sid else None


async def obtener_sesion(pool: asyncpg.Pool, sesion_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        sesion = await _sesion_completa(conn, sesion_id)
    if sesion is None:
        raise NoEncontradoError("Sesión de caja no encontrada")
    return sesion


async def listar_sesiones(pool: asyncpg.Pool, *, limite: int = 30) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT s.*, count(v.id) AS cantidad_ventas, COALESCE(sum(v.total), 0)::numeric(14, 2) AS total_ventas
            FROM soluciones.pos_sesiones s
            LEFT JOIN soluciones.pos_ventas v ON v.sesion_id = s.id
            GROUP BY s.id
            ORDER BY s.abierta_en DESC
            LIMIT $1
            """,
            limite,
        )
    resultado = []
    for f in filas:
        datos = _con_id_str(f)
        datos["almacen_id"] = str(datos["almacen_id"]) if datos["almacen_id"] else None
        datos["esperado"] = _decimales(datos["esperado"])
        datos["contado"] = _decimales(datos["contado"])
        datos["diferencias"] = (
            calc.diferencias_arqueo(datos["esperado"], datos["contado"]) if datos["contado"] is not None else None
        )
        datos["ventas"] = []
        resultado.append(datos)
    return resultado


async def registrar_venta(
    pool: asyncpg.Pool, sesion_id: uuid.UUID, *, factura_id: uuid.UUID, pagos: list[dict[str, Any]]
) -> dict[str, Any]:
    """Registra en la caja un e-CF YA emitido: enviado a la DGII (con
    trackId) o emitido en contingencia (firmado, se envía solo al
    reconectar; ver facturas_api app.contingencia). El total es el del e-CF
    guardado, no el que diga el cliente HTTP.

    Idempotente para la misma sesión: si la factura ya está registrada en
    ESTA caja (el POS sin conexión reintenta la sincronización tras un corte
    justo después de registrarla) devuelve esa venta, aunque la caja ya se
    haya cerrado; en otra caja, 409."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            sesion = await conn.fetchrow(
                "SELECT estado FROM soluciones.pos_sesiones WHERE id = $1 FOR UPDATE", sesion_id
            )
            if sesion is None:
                raise NoEncontradoError("Sesión de caja no encontrada")
            ya = await conn.fetchrow("SELECT * FROM soluciones.pos_ventas WHERE factura_id = $1", factura_id)
            if ya is not None:
                if ya["sesion_id"] != sesion_id:
                    raise ConflictoSolucionesError("Esta factura ya está registrada en otra caja")
                return _fila_venta(ya)
            if sesion["estado"] != "abierta":
                raise ConflictoSolucionesError("La sesión de caja ya está cerrada")
            factura = await conn.fetchrow(
                f"""
                SELECT f.id, f.encf, f.tipo_ecf, f.track_id, f.estado, {_TOTAL} AS total,
                       {_RNC_COMPRADOR} AS cliente_rnc, {_NOMBRE_COMPRADOR} AS cliente_nombre
                FROM facturacion.facturas f
                CROSS JOIN LATERAL (SELECT f.datos::jsonb AS d) x
                WHERE f.id = $1
                """,
                factura_id,
            )
            if factura is None:
                raise NoEncontradoError("Factura no encontrada")
            if factura["tipo_ecf"] not in (31, 32):
                raise DatosInvalidosError("El POS solo registra e-CF de consumo (32) o crédito fiscal (31)")
            if not factura["track_id"] and factura["estado"] != "contingencia":
                raise ConflictoSolucionesError("La factura aún no se ha enviado a la DGII")
            montos = calc.validar_pagos(factura["total"], pagos)
            pagos_json = [
                {"metodo": p["metodo"], "monto": str(calc.redondear(Decimal(p["monto"]))), "referencia": p.get("referencia")}
                for p in pagos
            ]
            try:
                fila = await conn.fetchrow(
                    """
                    INSERT INTO soluciones.pos_ventas
                        (sesion_id, factura_id, encf, tipo_ecf, cliente_rnc, cliente_nombre, total, pagos, recibido, cambio)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
                    RETURNING *
                    """,
                    sesion_id,
                    factura_id,
                    factura["encf"],
                    factura["tipo_ecf"],
                    factura["cliente_rnc"],
                    factura["cliente_nombre"],
                    factura["total"],
                    json.dumps(pagos_json),
                    montos["recibido"],
                    montos["cambio"],
                )
            except asyncpg.UniqueViolationError as exc:
                raise ConflictoSolucionesError("Esta factura ya está registrada en una caja") from exc
    return _fila_venta(fila)


async def cerrar_sesion(
    pool: asyncpg.Pool, sesion_id: uuid.UUID, *, contado: dict[str, Decimal], notas: str | None
) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            sesion = await _sesion_completa(conn, sesion_id)
            if sesion is None:
                raise NoEncontradoError("Sesión de caja no encontrada")
            if sesion["estado"] != "abierta":
                raise ConflictoSolucionesError("La sesión de caja ya está cerrada")
            esperado = calc.esperado_en_caja(sesion["fondo_inicial"], sesion["ventas"])
            contado_norm = {m: calc.redondear(Decimal(contado.get(m) or 0)) for m in calc.METODOS_POS}
            await conn.execute(
                """
                UPDATE soluciones.pos_sesiones
                SET estado = 'cerrada', cerrada_en = now(), esperado = $2::jsonb, contado = $3::jsonb, notas_cierre = $4
                WHERE id = $1
                """,
                sesion_id,
                json.dumps({k: str(v) for k, v in esperado.items()}),
                json.dumps({k: str(v) for k, v in contado_norm.items()}),
                notas,
            )
            return await _sesion_completa(conn, sesion_id)


async def factura_para_timbre(pool: asyncpg.Pool, factura_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            "SELECT id, rnc_emisor, encf, tipo_ecf, ambiente, xml_firmado, datos::jsonb AS d "
            "FROM facturacion.facturas WHERE id = $1",
            factura_id,
        )
    if fila is None:
        raise NoEncontradoError("Factura no encontrada")
    d = fila["d"]
    datos = json.loads(d) if isinstance(d, str) else d
    return {
        "rnc_emisor": fila["rnc_emisor"],
        "encf": fila["encf"],
        "ambiente": fila["ambiente"],
        "xml_firmado": fila["xml_firmado"],
        "datos": datos,
    }
