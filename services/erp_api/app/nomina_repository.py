"""Acceso a Postgres de Kontara Nómina (esquema `nomina`): empresa
empleadora, parámetros de ley por fecha de vigencia, empleados, períodos de
nómina con sus líneas (novedades + resultado), pagos, archivos (banco y
TSS) y reportes (resumen TSS, IR-3, IR-13, costo vs pago).

Modelo (ver docs/NOMINA.md):

- Un **período** (mensual, o quincena 1/2) incluye a los empleados cuyo
  `tipo_pago` coincide, que ya habían ingresado al terminar el período y
  que no habían salido antes de empezar. Vive en
  borrador -> calculada -> aprobada -> pagada -> cerrada.
- **Calcular** (y cada cambio de novedades) sincroniza las líneas con los
  empleados, copia sus datos (nombre, salario, cuenta) y aplica
  `nomina_calculos.calcular_linea` con los parámetros vigentes al inicio
  del período (copiados en `periodos.parametros`) y la tasa SRL de la
  empresa. Solo en borrador/calculada: una nómina aprobada no cambia.
- **Pagar** marca las líneas pagadas (todas o algunas); cuando no queda
  ninguna pendiente el período pasa a `pagada`. **Cerrar** la bloquea.

Igual que `repository.py`, este repositorio SÍ requiere una base de datos.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from . import nomina_calculos as calc
from .nomina_calculos import EntradaLinea, Parametros
from .repository import DatosInvalidosError, hoy

_CERO = Decimal("0")

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
    "noviembre", "diciembre",
)


class NominaNoEncontradaError(Exception):
    """Empleado, período, línea o parámetros inexistentes (404)."""


class ConflictoNominaError(Exception):
    """La operación no aplica en el estado actual (409)."""


def nombre_periodo(tipo: str, anio: int, mes: int, quincena: int | None) -> str:
    base = f"{MESES[mes - 1]} {anio}"
    if tipo == "quincenal":
        return f"{'1.ª' if quincena == 1 else '2.ª'} quincena de {base}"
    return base


def _solo_digitos(valor: str | None) -> str:
    return re.sub(r"\D", "", valor or "")


def _texto(valor: Any) -> str | None:
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


# --- Empresa ---------------------------------------------------------

_EMPRESA_DEFECTO: dict[str, Any] = {
    "rnc": None, "razon_social": None, "categoria": None, "municipio": None, "contacto_nombre": None,
    "tasa_srl": Decimal("0.0110"), "frecuencia_pago": "mensual", "dia_pago": 30, "banco": None,
    "cuenta_origen": None, "actualizado_en": None,
}

_CAMPOS_EMPRESA = (
    "rnc", "razon_social", "categoria", "municipio", "contacto_nombre", "tasa_srl", "frecuencia_pago", "dia_pago",
    "banco", "cuenta_origen",
)


def empresa_completa(empresa: dict[str, Any]) -> bool:
    return bool(empresa.get("rnc") and empresa.get("categoria") and empresa.get("municipio"))


async def _empresa(conn: asyncpg.Connection) -> dict[str, Any]:
    fila = await conn.fetchrow(f"SELECT {', '.join(_CAMPOS_EMPRESA)}, actualizado_en FROM nomina.empresa WHERE id = 1")
    datos = dict(_EMPRESA_DEFECTO) if fila is None else dict(fila)
    datos["completa"] = empresa_completa(datos)
    return datos


async def obtener_empresa(pool: asyncpg.Pool) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _empresa(conn)


async def guardar_empresa(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    rnc = _solo_digitos(campos.get("rnc")) or None
    if rnc is not None and len(rnc) not in (9, 11):
        raise DatosInvalidosError("El RNC de la empresa lleva 9 dígitos (o 11 si es una cédula)")
    valores = {**campos, "rnc": rnc}
    for k in ("razon_social", "categoria", "municipio", "contacto_nombre", "banco", "cuenta_origen"):
        valores[k] = _texto(valores.get(k))
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO nomina.empresa (id, {', '.join(_CAMPOS_EMPRESA)}, actualizado_en)
            VALUES (1, {', '.join(f'${i}' for i in range(1, len(_CAMPOS_EMPRESA) + 1))}, now())
            ON CONFLICT (id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in _CAMPOS_EMPRESA)},
                actualizado_en = now()
            """,
            *(valores[c] for c in _CAMPOS_EMPRESA),
        )
        return await _empresa(conn)


# --- Parámetros ---------------------------------------------------------

_CAMPOS_PARAMETROS = (
    "salario_minimo_cotizable", "afp_empleado", "afp_empleador", "sfs_empleado", "sfs_empleador", "srl_empleador",
    "infotep_empleador", "infotep_bonificacion_empleado", "tope_afp_salarios", "tope_sfs_salarios",
    "tope_srl_salarios", "escala_isr", "regalia_tope_exento", "divisor_salario_diario", "horas_jornada",
    "recargo_horas_extra", "recargo_horas_extra_exceso", "fuente",
)


def _escala(valor: Any) -> list[dict[str, Any]]:
    return json.loads(valor) if isinstance(valor, str) else list(valor)


def parametros_de_fila(fila: dict[str, Any] | asyncpg.Record) -> Parametros:
    f = dict(fila)
    return Parametros(
        vigente_desde=f["vigente_desde"] if isinstance(f["vigente_desde"], date)
        else date.fromisoformat(f["vigente_desde"]),
        salario_minimo_cotizable=Decimal(str(f["salario_minimo_cotizable"])),
        afp_empleado=Decimal(str(f["afp_empleado"])),
        afp_empleador=Decimal(str(f["afp_empleador"])),
        sfs_empleado=Decimal(str(f["sfs_empleado"])),
        sfs_empleador=Decimal(str(f["sfs_empleador"])),
        srl_empleador=Decimal(str(f["srl_empleador"])),
        infotep_empleador=Decimal(str(f["infotep_empleador"])),
        infotep_bonificacion_empleado=Decimal(str(f["infotep_bonificacion_empleado"])),
        tope_afp_salarios=Decimal(str(f["tope_afp_salarios"])),
        tope_sfs_salarios=Decimal(str(f["tope_sfs_salarios"])),
        tope_srl_salarios=Decimal(str(f["tope_srl_salarios"])),
        escala_isr=calc.escala_desde_json(_escala(f["escala_isr"])),
        regalia_tope_exento=None if f.get("regalia_tope_exento") is None else Decimal(str(f["regalia_tope_exento"])),
        divisor_salario_diario=Decimal(str(f["divisor_salario_diario"])),
        horas_jornada=Decimal(str(f["horas_jornada"])),
        recargo_horas_extra=Decimal(str(f["recargo_horas_extra"])),
        recargo_horas_extra_exceso=Decimal(str(f["recargo_horas_extra_exceso"])),
    )


def _fila_parametros(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos["escala_isr"] = _escala(datos["escala_isr"])
    p = parametros_de_fila(fila)
    datos.update(tope_afp=p.tope_afp, tope_sfs=p.tope_sfs, tope_srl=p.tope_srl, exento_isr_anual=p.exento_isr_anual)
    return datos


def _snapshot(fila: asyncpg.Record) -> str:
    """Copia JSON de una fila de parámetros (para `periodos.parametros`)."""
    datos = {k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in dict(fila).items() if k != "actualizado_en"}
    datos["escala_isr"] = _escala(datos["escala_isr"])
    return json.dumps(datos)


async def listar_parametros(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        filas = await conn.fetch("SELECT * FROM nomina.parametros ORDER BY vigente_desde DESC")
    return [_fila_parametros(f) for f in filas]


async def _parametros_vigentes(conn: asyncpg.Connection, fecha: date) -> asyncpg.Record:
    fila = await conn.fetchrow(
        "SELECT * FROM nomina.parametros WHERE vigente_desde <= $1 ORDER BY vigente_desde DESC LIMIT 1", fecha
    )
    if fila is None:
        raise DatosInvalidosError(
            f"No hay parámetros de nómina vigentes al {fecha:%d/%m/%Y}: agréguelos en Nómina › Configuración"
        )
    return fila


async def parametros_vigentes(pool: asyncpg.Pool, fecha: date | None = None) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return _fila_parametros(await _parametros_vigentes(conn, fecha or hoy()))


async def guardar_parametros(pool: asyncpg.Pool, vigente_desde: date, **campos: Any) -> dict[str, Any]:
    try:
        escala = calc.escala_desde_json(campos["escala_isr"])
    except (ValueError, KeyError, InvalidOperation, TypeError) as exc:
        raise DatosInvalidosError(f"Escala del ISR inválida: {exc}") from None
    valores = {**campos, "escala_isr": json.dumps(calc.escala_a_json(escala)), "fuente": _texto(campos.get("fuente"))}
    columnas = ("vigente_desde",) + _CAMPOS_PARAMETROS
    marcadores = ", ".join(f"${i}" + ("::jsonb" if c == "escala_isr" else "") for i, c in enumerate(columnas, 1))
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(
            f"""
            INSERT INTO nomina.parametros ({', '.join(columnas)}, actualizado_en) VALUES ({marcadores}, now())
            ON CONFLICT (vigente_desde) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in _CAMPOS_PARAMETROS)},
                actualizado_en = now()
            RETURNING *
            """,
            vigente_desde,
            *(valores[c] for c in _CAMPOS_PARAMETROS),
        )
    return _fila_parametros(fila)


async def eliminar_parametros(pool: asyncpg.Pool, vigente_desde: date) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            total = await conn.fetchval("SELECT count(*) FROM nomina.parametros")
            borrada = await conn.fetchval(
                "DELETE FROM nomina.parametros WHERE vigente_desde = $1 RETURNING vigente_desde", vigente_desde
            )
            if borrada is None:
                raise NominaNoEncontradaError
            if total <= 1:
                raise ConflictoNominaError("Debe quedar al menos una tabla de parámetros")


async def indicadores(pool: asyncpg.Pool, fecha: date | None = None) -> dict[str, Any]:
    """Salario mínimo cotizable y topes vigentes (tarjeta "Indicadores")."""
    p = await parametros_vigentes(pool, fecha)
    return {
        "fecha": fecha or hoy(),
        "vigente_desde": p["vigente_desde"],
        "salario_minimo_cotizable": p["salario_minimo_cotizable"],
        "tope_afp": p["tope_afp"],
        "tope_sfs": p["tope_sfs"],
        "tope_srl": p["tope_srl"],
        "exento_isr_anual": p["exento_isr_anual"],
        "exento_isr_mensual": calc.redondear(p["exento_isr_anual"] / 12),
    }


# --- Empleados ---------------------------------------------------------

_CAMPOS_EMPLEADO = (
    "cedula", "nombre", "cargo", "departamento", "fecha_ingreso", "salario_mensual", "tipo_pago", "banco",
    "cuenta_bancaria", "tipo_cuenta", "afp", "ars", "correo", "estado", "fecha_salida",
)

_SELECT_EMPLEADOS = f"SELECT id, {', '.join(_CAMPOS_EMPLEADO)}, creado_en, actualizado_en FROM nomina.empleados"


def _fila_empleado(fila: asyncpg.Record, ref: date | None = None) -> dict[str, Any]:
    datos = dict(fila)
    corte = datos.get("fecha_salida") or ref or hoy()
    datos["dias_vacaciones"] = calc.dias_vacaciones(datos["fecha_ingreso"], corte)
    return datos


def normalizar_empleado(campos: dict[str, Any]) -> dict[str, Any]:
    """Reglas de negocio comunes a crear, editar e importar."""
    datos = dict(campos)
    cedula = _solo_digitos(datos.get("cedula"))
    if len(cedula) != 11:
        raise DatosInvalidosError("La cédula lleva 11 dígitos (p. ej. 402-2345678-1)")
    datos["cedula"] = cedula
    datos["nombre"] = _texto(datos.get("nombre"))
    if not datos["nombre"]:
        raise DatosInvalidosError("Falta el nombre del empleado")
    for k in ("cargo", "departamento", "banco", "afp", "ars", "correo", "tipo_cuenta"):
        datos[k] = _texto(datos.get(k))
    datos["cuenta_bancaria"] = _texto(datos.get("cuenta_bancaria"))
    if datos["cuenta_bancaria"] and not re.fullmatch(r"[0-9-]{4,30}", datos["cuenta_bancaria"]):
        raise DatosInvalidosError("La cuenta bancaria solo lleva dígitos y guiones")
    if datos.get("salario_mensual") is None or Decimal(datos["salario_mensual"]) <= 0:
        raise DatosInvalidosError("El salario mensual debe ser mayor que cero")
    datos.setdefault("estado", "activo")
    datos.setdefault("tipo_pago", "mensual")
    if datos["estado"] == "activo":
        datos["fecha_salida"] = None
    elif datos.get("fecha_salida") is None:
        datos["fecha_salida"] = hoy()
    if datos.get("fecha_salida") and datos["fecha_salida"] < datos["fecha_ingreso"]:
        raise DatosInvalidosError("La fecha de salida es anterior a la de ingreso")
    return datos


def _duplicada(cedula: str) -> DatosInvalidosError:
    return DatosInvalidosError(f"Ya existe un empleado con la cédula {cedula[:3]}-{cedula[3:10]}-{cedula[10]}")


async def crear_empleado(pool: asyncpg.Pool, **campos: Any) -> dict[str, Any]:
    datos = normalizar_empleado(campos)
    async with pool.acquire() as conn:
        try:
            fila = await conn.fetchrow(
                f"""
                INSERT INTO nomina.empleados ({', '.join(_CAMPOS_EMPLEADO)})
                VALUES ({', '.join(f'${i}' for i in range(1, len(_CAMPOS_EMPLEADO) + 1))})
                RETURNING id, {', '.join(_CAMPOS_EMPLEADO)}, creado_en, actualizado_en
                """,
                *(datos.get(c) for c in _CAMPOS_EMPLEADO),
            )
        except asyncpg.UniqueViolationError:
            raise ConflictoNominaError(str(_duplicada(datos["cedula"]))) from None
    return _fila_empleado(fila)


async def listar_empleados(
    pool: asyncpg.Pool, *, q: str | None = None, estado: str | None = None, tipo_pago: str | None = None
) -> list[dict[str, Any]]:
    condiciones, args = [], []
    if q:
        args.append(f"%{q.strip().lower()}%")
        condiciones.append(
            f"(lower(nombre) LIKE ${len(args)} OR cedula LIKE ${len(args)} OR lower(coalesce(cargo, '')) LIKE ${len(args)}"
            f" OR lower(coalesce(departamento, '')) LIKE ${len(args)})"
        )
    if estado:
        args.append(estado)
        condiciones.append(f"estado = ${len(args)}")
    if tipo_pago:
        args.append(tipo_pago)
        condiciones.append(f"tipo_pago = ${len(args)}")
    where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
    async with pool.acquire() as conn:
        filas = await conn.fetch(f"{_SELECT_EMPLEADOS}{where} ORDER BY estado, lower(nombre)", *args)
    return [_fila_empleado(f) for f in filas]


async def obtener_empleado(pool: asyncpg.Pool, empleado_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        fila = await conn.fetchrow(f"{_SELECT_EMPLEADOS} WHERE id = $1", empleado_id)
    if fila is None:
        raise NominaNoEncontradaError
    return _fila_empleado(fila)


async def actualizar_empleado(pool: asyncpg.Pool, empleado_id: uuid.UUID, **campos: Any) -> dict[str, Any]:
    datos = normalizar_empleado(campos)
    sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(_CAMPOS_EMPLEADO, 2))
    async with pool.acquire() as conn:
        try:
            fila = await conn.fetchrow(
                f"""
                UPDATE nomina.empleados SET {sets}, actualizado_en = now() WHERE id = $1
                RETURNING id, {', '.join(_CAMPOS_EMPLEADO)}, creado_en, actualizado_en
                """,
                empleado_id,
                *(datos.get(c) for c in _CAMPOS_EMPLEADO),
            )
        except asyncpg.UniqueViolationError:
            raise ConflictoNominaError(str(_duplicada(datos["cedula"]))) from None
    if fila is None:
        raise NominaNoEncontradaError
    return _fila_empleado(fila)


async def eliminar_empleado(pool: asyncpg.Pool, empleado_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await conn.fetchrow("SELECT id FROM nomina.empleados WHERE id = $1 FOR UPDATE", empleado_id)
            if fila is None:
                raise NominaNoEncontradaError
            if await conn.fetchval("SELECT 1 FROM nomina.lineas WHERE empleado_id = $1 LIMIT 1", empleado_id):
                raise ConflictoNominaError(
                    "El empleado ya aparece en una nómina: dele de baja (estado inactivo) en vez de eliminarlo"
                )
            await conn.execute("DELETE FROM nomina.empleados WHERE id = $1", empleado_id)


# Encabezados aceptados en el CSV de importación (sin acentos ni mayúsculas).
_ALIAS_CSV = {
    "cedula": "cedula", "nombre": "nombre", "nombre completo": "nombre", "cargo": "cargo", "puesto": "cargo",
    "departamento": "departamento", "fecha ingreso": "fecha_ingreso", "fecha de ingreso": "fecha_ingreso",
    "fecha_ingreso": "fecha_ingreso", "salario": "salario_mensual", "salario mensual": "salario_mensual",
    "salario_mensual": "salario_mensual", "tipo pago": "tipo_pago", "tipo de pago": "tipo_pago",
    "tipo_pago": "tipo_pago", "frecuencia": "tipo_pago", "banco": "banco", "cuenta": "cuenta_bancaria",
    "cuenta bancaria": "cuenta_bancaria", "cuenta_bancaria": "cuenta_bancaria", "tipo cuenta": "tipo_cuenta",
    "tipo_cuenta": "tipo_cuenta", "afp": "afp", "ars": "ars", "correo": "correo", "email": "correo",
}


def _sin_acentos(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _fecha_csv(valor: str) -> date:
    valor = valor.strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            pass
    raise DatosInvalidosError(f"Fecha inválida «{valor}» (use AAAA-MM-DD o DD/MM/AAAA)")


def _monto_csv(valor: str) -> Decimal:
    limpio = re.sub(r"[^0-9.\-]", "", valor.replace("RD$", ""))
    try:
        return Decimal(limpio)
    except InvalidOperation:
        raise DatosInvalidosError(f"Monto inválido «{valor}»") from None


def parsear_csv_empleados(texto: str) -> tuple[list[tuple[int, dict[str, Any]]], list[dict[str, Any]]]:
    """CSV (coma o punto y coma, con encabezado) -> [(fila, campos)] y
    errores [{fila, mensaje}]. Columnas mínimas: cédula, nombre, salario y
    fecha de ingreso."""
    texto = texto.lstrip("﻿")
    if not texto.strip():
        raise DatosInvalidosError("El archivo está vacío")
    primera = texto.splitlines()[0]
    delimitador = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto), delimiter=delimitador)
    encabezado = [_ALIAS_CSV.get(_sin_acentos(h).strip().lower()) for h in next(lector)]
    faltan = {"cedula", "nombre", "salario_mensual", "fecha_ingreso"} - set(encabezado)
    if faltan:
        nombres = {"cedula": "cédula", "nombre": "nombre", "salario_mensual": "salario", "fecha_ingreso": "fecha ingreso"}
        raise DatosInvalidosError("Faltan columnas en el CSV: " + ", ".join(nombres[f] for f in sorted(faltan)))
    filas, errores = [], []
    for n, valores in enumerate(lector, start=2):
        if not any(v.strip() for v in valores):
            continue
        crudo = {c: v.strip() for c, v in zip(encabezado, valores) if c}
        try:
            campos: dict[str, Any] = {k: v for k, v in crudo.items() if k not in ("fecha_ingreso", "salario_mensual")}
            campos["fecha_ingreso"] = _fecha_csv(crudo.get("fecha_ingreso", ""))
            campos["salario_mensual"] = _monto_csv(crudo.get("salario_mensual", ""))
            tipo = _sin_acentos(crudo.get("tipo_pago", "") or "mensual").lower()
            if tipo not in ("mensual", "quincenal"):
                raise DatosInvalidosError(f"Tipo de pago «{crudo['tipo_pago']}»: use mensual o quincenal")
            campos["tipo_pago"] = tipo
            cuenta = _sin_acentos(crudo.get("tipo_cuenta", "") or "").lower() or None
            if cuenta not in (None, "ahorro", "corriente"):
                raise DatosInvalidosError(f"Tipo de cuenta «{crudo['tipo_cuenta']}»: use ahorro o corriente")
            campos["tipo_cuenta"] = cuenta
            filas.append((n, normalizar_empleado(campos)))
        except DatosInvalidosError as exc:
            errores.append({"fila": n, "mensaje": str(exc)})
    return filas, errores


async def importar_empleados(pool: asyncpg.Pool, texto: str) -> dict[str, Any]:
    """Crea los empleados nuevos y actualiza (por cédula) los existentes. Las
    filas con error se informan y no se importan; las demás sí."""
    filas, errores = parsear_csv_empleados(texto)
    creados = actualizados = 0
    vistas: set[str] = set()
    columnas = [c for c in _CAMPOS_EMPLEADO if c not in ("estado", "fecha_salida")]
    async with pool.acquire() as conn:
        async with conn.transaction():
            for n, datos in filas:
                if datos["cedula"] in vistas:
                    errores.append({"fila": n, "mensaje": "Cédula repetida en el archivo"})
                    continue
                vistas.add(datos["cedula"])
                nuevo = await conn.fetchval(
                    f"""
                    INSERT INTO nomina.empleados ({', '.join(columnas)})
                    VALUES ({', '.join(f'${i}' for i in range(1, len(columnas) + 1))})
                    ON CONFLICT (cedula) DO UPDATE SET
                        {', '.join(f'{c} = COALESCE(EXCLUDED.{c}, nomina.empleados.{c})' for c in columnas[1:])},
                        actualizado_en = now()
                    RETURNING (xmax = 0)
                    """,
                    *(datos.get(c) for c in columnas),
                )
                if nuevo:
                    creados += 1
                else:
                    actualizados += 1
    errores.sort(key=lambda e: e["fila"])
    return {"creados": creados, "actualizados": actualizados, "errores": errores}


async def prestaciones_empleado(
    pool: asyncpg.Pool, empleado_id: uuid.UUID, fecha_salida: date, desahucio: bool
) -> dict[str, Any]:
    empleado = await obtener_empleado(pool, empleado_id)
    async with pool.acquire() as conn:
        p = parametros_de_fila(await _parametros_vigentes(conn, fecha_salida))
    try:
        r = calc.prestaciones(empleado["salario_mensual"], empleado["fecha_ingreso"], fecha_salida, p, desahucio=desahucio)
    except ValueError as exc:
        raise DatosInvalidosError(str(exc)) from None
    return {"empleado_id": empleado_id, "fecha_salida": fecha_salida, "desahucio": desahucio, **r.__dict__}


# --- Períodos ---------------------------------------------------------

_COLUMNAS_PERIODO = (
    "id, tipo, anio, mes, quincena, fecha_inicio, fecha_fin, fecha_pago, estado, tasa_srl, notas, calculado_en,"
    " aprobado_en, pagado_en, cerrado_en, creado_en, parametros"
)

_TOTALES = (
    "salario", "horas_extra", "otros_ingresos", "bonificacion", "ingresos_exentos", "regalia", "total_ingresos",
    "afp", "sfs", "isr", "infotep_empleado", "otros_descuentos", "total_descuentos", "neto", "afp_empleador",
    "sfs_empleador", "srl", "infotep", "total_aportes_empleador", "costo_total",
)

_SELECT_PERIODOS = f"""
SELECT {', '.join('p.' + c.strip() for c in _COLUMNAS_PERIODO.split(','))},
       count(l.empleado_id) AS empleados,
       count(l.empleado_id) FILTER (WHERE l.pagado) AS pagados,
       {', '.join(f'coalesce(sum(l.{c}), 0) AS total_{c}' for c in _TOTALES)}
FROM nomina.periodos p
LEFT JOIN nomina.lineas l ON l.periodo_id = p.id
"""


def _fila_periodo(fila: asyncpg.Record) -> dict[str, Any]:
    datos = dict(fila)
    datos.pop("parametros", None)
    datos["nombre"] = nombre_periodo(datos["tipo"], datos["anio"], datos["mes"], datos["quincena"])
    datos["totales"] = {c: datos.pop(f"total_{c}") for c in _TOTALES}
    return datos


async def listar_periodos(pool: asyncpg.Pool, *, anio: int | None = None, estado: str | None = None) -> list[dict[str, Any]]:
    condiciones, args = [], []
    if anio:
        args.append(anio)
        condiciones.append(f"p.anio = ${len(args)}")
    if estado:
        args.append(estado)
        condiciones.append(f"p.estado = ${len(args)}")
    where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            f"{_SELECT_PERIODOS}{where} GROUP BY p.id ORDER BY p.anio DESC, p.mes DESC, coalesce(p.quincena, 3) DESC, p.tipo",
            *args,
        )
    return [_fila_periodo(f) for f in filas]


_COLUMNAS_LINEA = (
    "empleado_id, cedula, nombre, cargo, departamento, salario_mensual, banco, cuenta_bancaria, tipo_cuenta,"
    " horas_extra_35, horas_extra_100, otros_ingresos, bonificacion, ingresos_exentos, regalia, otros_descuentos,"
    " salario, horas_extra, total_ingresos, salario_cotizable, base_isr, afp, sfs, isr, infotep_empleado,"
    " total_descuentos, neto, afp_empleador, sfs_empleador, srl, infotep, total_aportes_empleador, costo_total,"
    " pagado, pagado_en, referencia_pago"
)


async def _periodo(conn: asyncpg.Connection, periodo_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(f"{_SELECT_PERIODOS} WHERE p.id = $1 GROUP BY p.id", periodo_id)
    if fila is None:
        raise NominaNoEncontradaError
    datos = _fila_periodo(fila)
    datos["parametros"] = json.loads(fila["parametros"]) if fila["parametros"] else None
    lineas = await conn.fetch(
        f"SELECT {_COLUMNAS_LINEA} FROM nomina.lineas WHERE periodo_id = $1 ORDER BY lower(nombre)", periodo_id
    )
    datos["lineas"] = [dict(linea) for linea in lineas]
    return datos


async def obtener_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        return await _periodo(conn, periodo_id)


async def _bloquear(conn: asyncpg.Connection, periodo_id: uuid.UUID) -> asyncpg.Record:
    fila = await conn.fetchrow(f"SELECT {_COLUMNAS_PERIODO} FROM nomina.periodos WHERE id = $1 FOR UPDATE", periodo_id)
    if fila is None:
        raise NominaNoEncontradaError
    return fila


def _exigir(fila: asyncpg.Record, estados: tuple[str, ...], accion: str) -> None:
    if fila["estado"] not in estados:
        nombre = nombre_periodo(fila["tipo"], fila["anio"], fila["mes"], fila["quincena"])
        raise ConflictoNominaError(f"La nómina de {nombre} está {fila['estado']}: no se puede {accion}")


async def crear_periodo(
    pool: asyncpg.Pool, *, tipo: str, anio: int, mes: int, quincena: int | None = None, fecha_pago: date | None = None,
    notas: str | None = None,
) -> dict[str, Any]:
    try:
        inicio, fin = calc.fechas_periodo(tipo, anio, mes, quincena)
    except ValueError as exc:
        raise DatosInvalidosError(str(exc)) from None
    async with pool.acquire() as conn:
        async with conn.transaction():
            if fecha_pago is None:
                empresa = await _empresa(conn)
                fecha_pago = fin if tipo == "quincenal" else date(anio, mes, min(empresa["dia_pago"], fin.day))
            if fecha_pago < inicio:
                raise DatosInvalidosError("La fecha de pago es anterior al inicio del período")
            try:
                periodo_id = await conn.fetchval(
                    """
                    INSERT INTO nomina.periodos (tipo, anio, mes, quincena, fecha_inicio, fecha_fin, fecha_pago, notas)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id
                    """,
                    tipo, anio, mes, quincena, inicio, fin, fecha_pago, _texto(notas),
                )
            except asyncpg.UniqueViolationError:
                raise ConflictoNominaError(
                    f"Ya existe la nómina {'quincenal' if tipo == 'quincenal' else 'mensual'} de "
                    f"{nombre_periodo(tipo, anio, mes, quincena)}"
                ) from None
            await _sincronizar_lineas(conn, periodo_id, tipo, inicio, fin)
            return await _periodo(conn, periodo_id)


async def _sincronizar_lineas(conn: asyncpg.Connection, periodo_id: uuid.UUID, tipo: str, inicio: date, fin: date) -> None:
    """Agrega a los empleados que corresponden al período, quita a los que ya
    no (dados de baja antes del inicio, cambiados de tipo de pago) y
    refresca la copia de sus datos."""
    await conn.execute(
        """
        DELETE FROM nomina.lineas l USING nomina.empleados e
        WHERE l.periodo_id = $1 AND e.id = l.empleado_id
          AND (e.tipo_pago <> $2 OR e.fecha_ingreso > $4 OR (e.estado = 'inactivo' AND coalesce(e.fecha_salida, $3) < $3))
        """,
        periodo_id, tipo, inicio, fin,
    )
    await conn.execute(
        """
        INSERT INTO nomina.lineas (periodo_id, empleado_id, cedula, nombre, cargo, departamento, salario_mensual,
                                   banco, cuenta_bancaria, tipo_cuenta)
        SELECT $1, e.id, e.cedula, e.nombre, e.cargo, e.departamento, e.salario_mensual, e.banco, e.cuenta_bancaria,
               e.tipo_cuenta
        FROM nomina.empleados e
        WHERE e.tipo_pago = $2 AND e.fecha_ingreso <= $4
          AND (e.estado = 'activo' OR coalesce(e.fecha_salida, $3) >= $3)
        ON CONFLICT (periodo_id, empleado_id) DO UPDATE SET
            cedula = EXCLUDED.cedula, nombre = EXCLUDED.nombre, cargo = EXCLUDED.cargo,
            departamento = EXCLUDED.departamento, salario_mensual = EXCLUDED.salario_mensual, banco = EXCLUDED.banco,
            cuenta_bancaria = EXCLUDED.cuenta_bancaria, tipo_cuenta = EXCLUDED.tipo_cuenta
        """,
        periodo_id, tipo, inicio, fin,
    )


def proporcion_dias(inicio: date, fin: date, ingreso: date, salida: date | None) -> Decimal:
    """Parte del período que el empleado trabajó (ingreso o salida a mitad del
    período), por días calendario. 1 si estuvo todo el período."""
    desde = max(inicio, ingreso)
    hasta = min(fin, salida) if salida else fin
    if hasta < desde:
        return _CERO
    dias = (hasta - desde).days + 1
    total = (fin - inicio).days + 1
    return Decimal("1") if dias >= total else Decimal(dias) / Decimal(total)


async def _calcular(conn: asyncpg.Connection, periodo: asyncpg.Record) -> None:
    periodo_id = periodo["id"]
    await _sincronizar_lineas(conn, periodo_id, periodo["tipo"], periodo["fecha_inicio"], periodo["fecha_fin"])
    fila_p = await _parametros_vigentes(conn, periodo["fecha_inicio"])
    p = parametros_de_fila(fila_p)
    empresa = await _empresa(conn)
    tasa_srl = Decimal(empresa["tasa_srl"])
    fraccion = calc.fraccion_periodo(periodo["tipo"])
    lineas = await conn.fetch(
        """
        SELECT l.*, e.fecha_ingreso, e.fecha_salida, e.estado AS estado_empleado
        FROM nomina.lineas l JOIN nomina.empleados e ON e.id = l.empleado_id
        WHERE l.periodo_id = $1
        """,
        periodo_id,
    )
    for linea in lineas:
        salida = linea["fecha_salida"] if linea["estado_empleado"] == "inactivo" else None
        proporcion = proporcion_dias(periodo["fecha_inicio"], periodo["fecha_fin"], linea["fecha_ingreso"], salida)
        entrada = EntradaLinea(
            salario_mensual=linea["salario_mensual"],
            tasa_srl=tasa_srl,
            fraccion=fraccion,
            proporcion=proporcion,
            horas_extra_35=linea["horas_extra_35"],
            horas_extra_100=linea["horas_extra_100"],
            otros_ingresos=linea["otros_ingresos"],
            bonificacion=linea["bonificacion"],
            ingresos_exentos=linea["ingresos_exentos"],
            regalia=linea["regalia"],
            otros_descuentos=linea["otros_descuentos"],
        )
        r = calc.calcular_linea(entrada, p)
        if r.neto < 0:
            raise DatosInvalidosError(
                f"El neto de {linea['nombre']} queda negativo (RD$ {r.neto:,.2f}): revise sus otros descuentos"
            )
        await conn.execute(
            """
            UPDATE nomina.lineas SET salario = $3, horas_extra = $4, total_ingresos = $5, salario_cotizable = $6,
                base_isr = $7, afp = $8, sfs = $9, isr = $10, infotep_empleado = $11, total_descuentos = $12,
                neto = $13, afp_empleador = $14, sfs_empleador = $15, srl = $16, infotep = $17,
                total_aportes_empleador = $18, costo_total = $19
            WHERE periodo_id = $1 AND empleado_id = $2
            """,
            periodo_id, linea["empleado_id"], r.salario, r.horas_extra, r.total_ingresos, r.salario_cotizable,
            r.base_isr, r.afp, r.sfs, r.isr, r.infotep_empleado, r.total_descuentos, r.neto, r.afp_empleador,
            r.sfs_empleador, r.srl, r.infotep, r.total_aportes_empleador, r.costo_total,
        )
    await conn.execute(
        """
        UPDATE nomina.periodos SET estado = 'calculada', calculado_en = now(), parametros = $2::jsonb, tasa_srl = $3
        WHERE id = $1
        """,
        periodo_id, _snapshot(fila_p), tasa_srl,
    )


async def calcular_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("borrador", "calculada"), "recalcular")
            await _calcular(conn, fila)
            return await _periodo(conn, periodo_id)


_CAMPOS_NOVEDADES = (
    "horas_extra_35", "horas_extra_100", "otros_ingresos", "bonificacion", "ingresos_exentos", "regalia",
    "otros_descuentos",
)


async def guardar_novedades(
    pool: asyncpg.Pool, periodo_id: uuid.UUID, empleado_id: uuid.UUID, **campos: Decimal
) -> dict[str, Any]:
    """Guarda las novedades de un empleado y recalcula el período."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("borrador", "calculada"), "modificar")
            actualizada = await conn.fetchval(
                f"""
                UPDATE nomina.lineas SET {', '.join(f'{c} = ${i}' for i, c in enumerate(_CAMPOS_NOVEDADES, 3))}
                WHERE periodo_id = $1 AND empleado_id = $2 RETURNING empleado_id
                """,
                periodo_id, empleado_id, *(campos.get(c, _CERO) for c in _CAMPOS_NOVEDADES),
            )
            if actualizada is None:
                raise NominaNoEncontradaError
            await _calcular(conn, fila)
            return await _periodo(conn, periodo_id)


async def regalia_sugerida(pool: asyncpg.Pool, periodo_id: uuid.UUID, empleado_id: uuid.UUID) -> dict[str, Any]:
    """Regalía pascual del año del período: 1/12 del salario ordinario de las
    nóminas del año ya calculadas (sin contar este período) más el salario
    de los meses que faltan hasta diciembre, o la estimación proporcional
    con el salario actual si aún no tiene nóminas."""
    async with pool.acquire() as conn:
        periodo = await conn.fetchrow("SELECT anio, fecha_fin FROM nomina.periodos WHERE id = $1", periodo_id)
        empleado = await conn.fetchrow(
            "SELECT salario_mensual, fecha_ingreso, fecha_salida, estado FROM nomina.empleados WHERE id = $1",
            empleado_id,
        )
        if periodo is None or empleado is None:
            raise NominaNoEncontradaError
        anio = periodo["anio"]
        corte = empleado["fecha_salida"] if empleado["estado"] == "inactivo" else date(anio, 12, 31)
        estimada = calc.regalia_estimada(empleado["salario_mensual"], empleado["fecha_ingreso"], anio, corte)
        pagado = await conn.fetchval(
            """
            SELECT coalesce(sum(l.salario), 0) FROM nomina.lineas l JOIN nomina.periodos p ON p.id = l.periodo_id
            WHERE l.empleado_id = $1 AND p.anio = $2 AND p.id <> $3 AND l.salario IS NOT NULL
            """,
            empleado_id, anio, periodo_id,
        )
    return {
        "empleado_id": empleado_id, "anio": anio, "salario_ordinario_registrado": pagado,
        "regalia_registrada": calc.regalia_pascual([pagado]), "regalia": estimada,
    }


async def aprobar_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("calculada",), "aprobar (calcúlela primero)" if fila["estado"] == "borrador" else "aprobar")
            if not await conn.fetchval("SELECT 1 FROM nomina.lineas WHERE periodo_id = $1 LIMIT 1", periodo_id):
                raise ConflictoNominaError("La nómina no tiene empleados: registre empleados y vuelva a calcularla")
            await conn.execute("UPDATE nomina.periodos SET estado = 'aprobada', aprobado_en = now() WHERE id = $1", periodo_id)
            return await _periodo(conn, periodo_id)


async def reabrir_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("aprobada",), "reabrir")
            if await conn.fetchval("SELECT 1 FROM nomina.lineas WHERE periodo_id = $1 AND pagado LIMIT 1", periodo_id):
                raise ConflictoNominaError("Ya hay pagos registrados en esta nómina: no se puede reabrir")
            await conn.execute("UPDATE nomina.periodos SET estado = 'calculada', aprobado_en = NULL WHERE id = $1", periodo_id)
            return await _periodo(conn, periodo_id)


async def pagar_periodo(
    pool: asyncpg.Pool, periodo_id: uuid.UUID, *, empleado_ids: list[uuid.UUID] | None = None,
    referencia: str | None = None,
) -> dict[str, Any]:
    """Marca pagadas las líneas indicadas (o todas). Con todas pagadas, el
    período queda `pagada`."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("aprobada",), "registrar el pago (apruébela primero)" if fila["estado"] in ("borrador", "calculada") else "registrar el pago")
            if empleado_ids is not None:
                existentes = await conn.fetch(
                    "SELECT empleado_id FROM nomina.lineas WHERE periodo_id = $1 AND empleado_id = ANY($2::uuid[])",
                    periodo_id, empleado_ids,
                )
                if len(existentes) != len(set(empleado_ids)):
                    raise DatosInvalidosError("Alguno de los empleados no está en esta nómina")
            await conn.execute(
                """
                UPDATE nomina.lineas SET pagado = true, pagado_en = now(), referencia_pago = $3
                WHERE periodo_id = $1 AND NOT pagado AND ($2::uuid[] IS NULL OR empleado_id = ANY($2::uuid[]))
                """,
                periodo_id, empleado_ids, _texto(referencia),
            )
            pendientes = await conn.fetchval(
                "SELECT count(*) FROM nomina.lineas WHERE periodo_id = $1 AND NOT pagado", periodo_id
            )
            if pendientes == 0:
                await conn.execute("UPDATE nomina.periodos SET estado = 'pagada', pagado_en = now() WHERE id = $1", periodo_id)
            return await _periodo(conn, periodo_id)


async def cerrar_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> dict[str, Any]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("pagada",), "cerrar (registre el pago primero)" if fila["estado"] != "cerrada" else "cerrar")
            await conn.execute("UPDATE nomina.periodos SET estado = 'cerrada', cerrado_en = now() WHERE id = $1", periodo_id)
            return await _periodo(conn, periodo_id)


async def eliminar_periodo(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            fila = await _bloquear(conn, periodo_id)
            _exigir(fila, ("borrador", "calculada"), "eliminar")
            await conn.execute("DELETE FROM nomina.periodos WHERE id = $1", periodo_id)


# --- Archivos ---------------------------------------------------------

def _csv(filas: list[list[Any]]) -> bytes:
    """CSV con BOM (Excel lo abre con acentos) y fin de línea CRLF."""
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    for fila in filas:
        escritor.writerow(["" if v is None else (f"{v:.2f}" if isinstance(v, Decimal) else v) for v in fila])
    return ("﻿" + salida.getvalue()).encode("utf-8")


def _sufijo(tipo: str, anio: int, mes: int, quincena: int | None) -> str:
    return f"{anio}{mes:02d}" + (f"_q{quincena}" if quincena else "")


async def archivo_banco(pool: asyncpg.Pool, periodo_id: uuid.UUID) -> tuple[str, bytes]:
    """CSV genérico de dispersión: una fila por empleado con su cuenta y el
    neto. No es el formato propietario de ningún banco: sirve para cargarlo
    en la plantilla del banco o como respaldo."""
    async with pool.acquire() as conn:
        periodo = await _periodo(conn, periodo_id)
        empresa = await _empresa(conn)
    if periodo["estado"] not in ("aprobada", "pagada", "cerrada"):
        raise ConflictoNominaError("Apruebe la nómina antes de descargar el archivo para el banco")
    filas: list[list[Any]] = [[
        "RNC empresa", "Cuenta origen", "Fecha de pago", "Cédula", "Nombre", "Banco", "Tipo de cuenta",
        "Cuenta destino", "Monto", "Concepto",
    ]]
    sin_cuenta = []
    for linea in periodo["lineas"]:
        if not linea["cuenta_bancaria"]:
            sin_cuenta.append(linea["nombre"])
        filas.append([
            empresa.get("rnc") or "", empresa.get("cuenta_origen") or "", periodo["fecha_pago"].isoformat(),
            linea["cedula"], linea["nombre"], linea["banco"] or "", linea["tipo_cuenta"] or "",
            linea["cuenta_bancaria"] or "", linea["neto"] or _CERO, f"Nómina {periodo['nombre']}",
        ])
    nombre = f"nomina_{_sufijo(periodo['tipo'], periodo['anio'], periodo['mes'], periodo['quincena'])}_banco.csv"
    return nombre, _csv(filas)


_ESTADOS_REPORTABLES = ("aprobada", "pagada", "cerrada")


async def _lineas_mes(conn: asyncpg.Connection, anio: int, mes: int | None) -> list[asyncpg.Record]:
    """Líneas de las nóminas aprobadas/pagadas/cerradas del mes (o del año),
    sumadas por empleado."""
    return await conn.fetch(
        f"""
        SELECT l.empleado_id, max(l.cedula) AS cedula, max(l.nombre) AS nombre,
               {', '.join(f'coalesce(sum(l.{c}), 0) AS {c}' for c in _TOTALES + ('salario_cotizable', 'base_isr'))}
        FROM nomina.lineas l JOIN nomina.periodos p ON p.id = l.periodo_id
        WHERE p.anio = $1 AND ($2::int IS NULL OR p.mes = $2) AND p.estado = ANY($3::text[])
        GROUP BY l.empleado_id ORDER BY lower(max(l.nombre))
        """,
        anio, mes, list(_ESTADOS_REPORTABLES),
    )


async def _pendientes_mes(conn: asyncpg.Connection, anio: int, mes: int | None) -> int:
    return await conn.fetchval(
        """
        SELECT count(*) FROM nomina.periodos
        WHERE anio = $1 AND ($2::int IS NULL OR mes = $2) AND estado IN ('borrador', 'calculada')
        """,
        anio, mes,
    )


def _validar_mes(anio: int, mes: int | None) -> None:
    if not 2000 <= anio <= 2100 or (mes is not None and not 1 <= mes <= 12):
        raise DatosInvalidosError("Año o mes inválido")


async def reporte_tss(pool: asyncpg.Pool, anio: int, mes: int) -> dict[str, Any]:
    """Resumen de la autodeterminación mensual de la TSS: por empleado, el
    salario cotizable (salario + horas extra + comisiones), la regalía (va en
    su propia columna, no cotiza) y los aportes."""
    _validar_mes(anio, mes)
    async with pool.acquire() as conn:
        filas = await _lineas_mes(conn, anio, mes)
        pendientes = await _pendientes_mes(conn, anio, mes)
        p = parametros_de_fila(await _parametros_vigentes(conn, date(anio, mes, 1)))
    empleados = [
        {
            "empleado_id": f["empleado_id"], "cedula": f["cedula"], "nombre": f["nombre"],
            "salario_cotizable": f["salario_cotizable"], "regalia": f["regalia"],
            "otras_remuneraciones": f["bonificacion"] + f["ingresos_exentos"],
            "afp": f["afp"], "sfs": f["sfs"], "afp_empleador": f["afp_empleador"], "sfs_empleador": f["sfs_empleador"],
            "srl": f["srl"], "infotep": f["infotep"] + f["infotep_empleado"],
            "total": f["afp"] + f["sfs"] + f["afp_empleador"] + f["sfs_empleador"] + f["srl"],
        }
        for f in filas
    ]
    suma = {k: sum((e[k] for e in empleados), _CERO) for k in (
        "salario_cotizable", "regalia", "otras_remuneraciones", "afp", "sfs", "afp_empleador", "sfs_empleador", "srl",
        "infotep", "total",
    )}
    return {
        "anio": anio, "mes": mes, "nombre": f"{MESES[mes - 1]} {anio}", "nominas_pendientes": pendientes,
        "tope_afp": p.tope_afp, "tope_sfs": p.tope_sfs, "tope_srl": p.tope_srl, "empleados": empleados, "totales": suma,
        "vence": vencimiento_tss(anio, mes),
    }


async def archivo_tss(pool: asyncpg.Pool, anio: int, mes: int) -> tuple[str, bytes]:
    """CSV con las columnas de la autodeterminación (AM). NO es el archivo
    de ancho fijo del SUIR+: la TSS no publica la especificación vigente en
    abierto (ver docs/NOMINA.md); sirve para llenar la plantilla de la TSS."""
    r = await reporte_tss(pool, anio, mes)
    filas: list[list[Any]] = [[
        "Tipo documento", "Número documento", "Nombre", "Salario cotizable", "Aporte voluntario", "Salario ISR",
        "Otras remuneraciones", "Regalía pascual", "AFP empleado", "SFS empleado", "AFP empleador", "SFS empleador",
        "SRL", "INFOTEP",
    ]]
    for e in r["empleados"]:
        filas.append([
            "C", e["cedula"], e["nombre"], e["salario_cotizable"], _CERO, e["salario_cotizable"],
            e["otras_remuneraciones"], e["regalia"], e["afp"], e["sfs"], e["afp_empleador"], e["sfs_empleador"], e["srl"],
            e["infotep"],
        ])
    return f"tss_autodeterminacion_{anio}{mes:02d}.csv", _csv(filas)


async def reporte_ir3(pool: asyncpg.Pool, anio: int, mes: int) -> dict[str, Any]:
    """Pre-cálculo del IR-3 (retenciones de asalariados del mes): pagos
    gravados, exentos y el ISR retenido."""
    _validar_mes(anio, mes)
    async with pool.acquire() as conn:
        filas = await _lineas_mes(conn, anio, mes)
        pendientes = await _pendientes_mes(conn, anio, mes)
    empleados = [
        {
            "empleado_id": f["empleado_id"], "cedula": f["cedula"], "nombre": f["nombre"],
            "ingresos_gravados": f["salario_cotizable"] + f["bonificacion"],
            "ingresos_exentos": f["ingresos_exentos"] + f["regalia"],
            "tss": f["afp"] + f["sfs"], "base_isr": f["base_isr"], "isr": f["isr"],
        }
        for f in filas
    ]
    totales = {k: sum((e[k] for e in empleados), _CERO) for k in ("ingresos_gravados", "ingresos_exentos", "tss", "base_isr", "isr")}
    sig_anio, sig_mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    return {
        "anio": anio, "mes": mes, "nombre": f"{MESES[mes - 1]} {anio}", "nominas_pendientes": pendientes,
        "empleados": empleados, "totales": totales, "asalariados": len(empleados),
        "retenidos": sum(1 for e in empleados if e["isr"] > 0), "vence": date(sig_anio, sig_mes, 10),
    }


async def reporte_ir13(pool: asyncpg.Pool, anio: int) -> dict[str, Any]:
    """Resumen anual por empleado para la declaración IR-13."""
    _validar_mes(anio, None)
    async with pool.acquire() as conn:
        filas = await _lineas_mes(conn, anio, None)
        pendientes = await _pendientes_mes(conn, anio, None)
    empleados = [
        {
            "empleado_id": f["empleado_id"], "cedula": f["cedula"], "nombre": f["nombre"],
            "salarios": f["salario_cotizable"] + f["bonificacion"],
            "exentos": f["ingresos_exentos"] + f["regalia"], "tss": f["afp"] + f["sfs"],
            "renta_neta": f["base_isr"], "isr": f["isr"],
        }
        for f in filas
    ]
    totales = {k: sum((e[k] for e in empleados), _CERO) for k in ("salarios", "exentos", "tss", "renta_neta", "isr")}
    return {"anio": anio, "nominas_pendientes": pendientes, "empleados": empleados, "totales": totales}


async def costo_vs_pago(pool: asyncpg.Pool, anio: int) -> list[dict[str, Any]]:
    """Por mes del año: costo total para la empresa y pago neto a empleados de
    las nóminas calculadas en adelante."""
    _validar_mes(anio, None)
    async with pool.acquire() as conn:
        filas = await conn.fetch(
            """
            SELECT p.mes, coalesce(sum(l.costo_total), 0) AS costo, coalesce(sum(l.neto), 0) AS neto,
                   coalesce(sum(l.total_ingresos), 0) AS ingresos, count(DISTINCT l.empleado_id) AS empleados
            FROM nomina.periodos p JOIN nomina.lineas l ON l.periodo_id = p.id
            WHERE p.anio = $1 AND p.estado <> 'borrador'
            GROUP BY p.mes ORDER BY p.mes
            """,
            anio,
        )
    por_mes = {f["mes"]: f for f in filas}
    return [
        {
            "mes": m, "nombre": MESES[m - 1],
            "costo": por_mes[m]["costo"] if m in por_mes else _CERO,
            "neto": por_mes[m]["neto"] if m in por_mes else _CERO,
            "ingresos": por_mes[m]["ingresos"] if m in por_mes else _CERO,
            "empleados": por_mes[m]["empleados"] if m in por_mes else 0,
        }
        for m in range(1, 13)
    ]


# --- Resumen y avisos ---------------------------------------------------------

def vencimiento_tss(anio: int, mes: int) -> date:
    """La factura de la TSS se paga a más tardar el tercer día hábil del mes
    siguiente (sin contar feriados: verifique el calendario de la TSS)."""
    d = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
    habiles = 0
    while True:
        if d.weekday() < 5:
            habiles += 1
            if habiles == 3:
                return d
        d += timedelta(days=1)


async def resumen(pool: asyncpg.Pool) -> dict[str, Any]:
    """Datos de Nómina › Inicio: pasos de la guía, el período a calcular con
    sus totales e indicadores vigentes."""
    hoy_rd = hoy()
    async with pool.acquire() as conn:
        empresa = await _empresa(conn)
        empleados = await conn.fetchval("SELECT count(*) FROM nomina.empleados WHERE estado = 'activo'")
        alguna_calculada = await conn.fetchval("SELECT 1 FROM nomina.periodos WHERE estado <> 'borrador' LIMIT 1")
        # El período a mostrar: el más antiguo sin pagar; si no hay, el último (o ninguno).
        abierto = await conn.fetchval(
            """
            SELECT id FROM nomina.periodos WHERE estado IN ('borrador', 'calculada', 'aprobada')
            ORDER BY anio, mes, coalesce(quincena, 0) LIMIT 1
            """
        ) or await conn.fetchval(
            "SELECT id FROM nomina.periodos ORDER BY anio DESC, mes DESC, coalesce(quincena, 0) DESC LIMIT 1"
        )
        periodo = None
        if abierto:
            periodo = await _periodo(conn, abierto)
            periodo.pop("lineas")
            periodo.pop("parametros")
    try:
        ind = await indicadores(pool, hoy_rd)
    except DatosInvalidosError:
        ind = None
    return {
        "empresa_completa": empresa["completa"],
        "contacto_nombre": empresa.get("contacto_nombre"),
        "empleados_activos": empleados,
        "nomina_calculada": bool(alguna_calculada),
        "pasos_completados": 1 + int(empresa["completa"]) + int(empleados > 0) + int(bool(alguna_calculada)),
        "periodo": periodo,
        "periodo_sugerido": {"tipo": empresa["frecuencia_pago"], "anio": hoy_rd.year, "mes": hoy_rd.month,
                             "nombre": f"{MESES[hoy_rd.month - 1]} {hoy_rd.year}"},
        "indicadores": ind,
    }


async def avisos(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Bandeja de entrada de Nómina: avisos calculados (no se guardan) de
    nóminas por aprobar/pagar y vencimientos de la TSS y del IR-3."""
    hoy_rd = hoy()
    lista: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        empresa = await _empresa(conn)
        periodos = await conn.fetch(
            "SELECT id, tipo, anio, mes, quincena, estado, fecha_pago FROM nomina.periodos "
            "WHERE estado IN ('calculada', 'aprobada') ORDER BY anio, mes, coalesce(quincena, 0)"
        )
        meses = await conn.fetch(
            "SELECT DISTINCT anio, mes FROM nomina.periodos WHERE estado = ANY($1::text[]) ORDER BY anio DESC, mes DESC LIMIT 3",
            list(_ESTADOS_REPORTABLES),
        )
    if not empresa["completa"]:
        lista.append({"id": "empresa", "tipo": "equipo", "titulo": "Completa los datos de tu empresa",
                      "detalle": "RNC, categoría y municipio son necesarios para la TSS y la DGII.", "fecha": None,
                      "enlace": "/nomina/configuracion"})
    for p in periodos:
        nombre = nombre_periodo(p["tipo"], p["anio"], p["mes"], p["quincena"])
        if p["estado"] == "calculada":
            lista.append({"id": f"aprobar-{p['id']}", "tipo": "equipo", "titulo": f"Nómina de {nombre} por aprobar",
                          "detalle": f"Revisa y aprueba la nómina antes del pago del {p['fecha_pago']:%d/%m/%Y}.",
                          "fecha": p["fecha_pago"], "enlace": f"/nomina/periodos/{p['id']}"})
        else:
            lista.append({"id": f"pagar-{p['id']}", "tipo": "equipo", "titulo": f"Nómina de {nombre} por pagar",
                          "detalle": f"Aprobada · pago programado para el {p['fecha_pago']:%d/%m/%Y}.",
                          "fecha": p["fecha_pago"], "enlace": "/nomina/pagos"})
    for m in meses:
        anio, mes = m["anio"], m["mes"]
        tss = vencimiento_tss(anio, mes)
        sig = date(anio + 1, 1, 10) if mes == 12 else date(anio, mes + 1, 10)
        if tss >= hoy_rd - timedelta(days=5):
            lista.append({"id": f"tss-{anio}{mes:02d}", "tipo": "tss", "titulo": "TSS · Pago de la autodeterminación",
                          "detalle": f"La factura de la TSS de {MESES[mes - 1]} {anio} vence el {tss:%d/%m/%Y} "
                                     "(tercer día hábil del mes siguiente).",
                          "fecha": tss, "enlace": f"/nomina/reportes?anio={anio}&mes={mes}"})
        if sig >= hoy_rd - timedelta(days=5):
            lista.append({"id": f"ir3-{anio}{mes:02d}", "tipo": "dgii", "titulo": "DGII · Recordatorio IR-3",
                          "detalle": f"Las retenciones de ISR a asalariados de {MESES[mes - 1]} {anio} vencen el "
                                     f"{sig:%d/%m/%Y}.",
                          "fecha": sig, "enlace": f"/nomina/reportes?anio={anio}&mes={mes}"})
    return lista
