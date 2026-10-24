import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../lib/api";
import { CODIGOS_MODIFICACION, TIPOS_INGRESOS } from "../lib/catalogos";
import { MENSAJE_CONTINGENCIA } from "../lib/estados";
import { erpApi, ErpApiError } from "../lib/erpApi";
import type { Cliente, Cotizacion } from "../lib/erpApi";
import { configuracionApi, publicarEmpresa, type Secuencia, type Vendedor } from "../lib/configuracionApi";
import { calcularMontoItem, decimal2 } from "../lib/format";
import { configParaTipo } from "../lib/tipoConfig";
import {
  construirDatos,
  construirItemsInventario,
  datosAFormState,
  desglose,
  estadoInicial,
  INDICADOR_POR_TASA,
  itemsDesdeCotizacion,
  nuevoItem,
  siguienteEncf,
} from "../lib/buildDatos";
import type { FormState, ItemFormulario } from "../lib/buildDatos";
import { inventarioApi } from "../lib/inventarioApi";
import { formatoMonto, formatoRnc } from "../lib/marca";
import {
  buscarCliente,
  guardarCliente,
  guardarEmisor,
  guardarVencimientoSecuencia,
  leerEmisor,
  leerVencimientoSecuencia,
} from "../lib/preferencias";
import type { DatosEmisor } from "../lib/preferencias";
import { useFacturas } from "../lib/useFacturas";
import type { Almacen, FacturaDetalle, ItemFactura, Lote, Producto, SugerenciaFefo } from "../lib/types";
import { FacturaAcciones } from "./FacturaAcciones";

/** Tipos de e-CF de venta (los que muestra el diseño) y los de compra/pago, que se eligen aparte. */
const TIPOS_VENTA = [
  { code: "31", label: "Crédito fiscal", desc: "Ventas a empresas con derecho a crédito de ITBIS" },
  { code: "32", label: "Consumo", desc: "Consumidor final; permite cliente genérico" },
  { code: "33", label: "Nota de débito", desc: "Aumenta el monto de un e-CF emitido" },
  { code: "34", label: "Nota de crédito", desc: "Anula o reduce un e-CF emitido" },
  { code: "44", label: "Regímenes especiales", desc: "Zonas francas y otros regímenes" },
  { code: "45", label: "Gubernamental", desc: "Ventas a entidades del Estado" },
  { code: "46", label: "Exportaciones", desc: "Ventas de bienes al exterior" },
];
const TIPOS_OTROS = [
  { code: "41", label: "Compras", desc: "Compras a proveedores no inscritos" },
  { code: "43", label: "Gastos menores", desc: "Gastos menores sin comprobante" },
  { code: "47", label: "Pagos al exterior", desc: "Pagos a proveedores del exterior" },
];

/** Forma de pago del diseño -> código FormaPago de la DGII. Crédito implica TipoPago 2. */
const PAGOS = [
  { value: "1", label: "Efectivo" },
  { value: "3", label: "Tarjeta" },
  { value: "2", label: "Transferencia" },
  { value: "4", label: "Crédito" },
];

const TASAS = [
  { value: "1", label: "18%", tasa: 0.18 },
  { value: "2", label: "16%", tasa: 0.16 },
  { value: "3", label: "0%", tasa: 0 },
  { value: "4", label: "Exento", tasa: 0 },
];

/** Monto a partir del cual una factura de consumo debe identificar al comprador. */
const TOPE_CONSUMO_ANONIMO = 250000;

function sumarDias(fechaIso: string, dias: number): string {
  const d = new Date(`${fechaIso}T12:00:00`);
  d.setDate(d.getDate() + dias);
  return d.toISOString().slice(0, 10);
}

function fechaLegible(fechaIso: string): string {
  if (!fechaIso) return "";
  const [a, m, d] = fechaIso.split("-");
  return `${d}/${m}/${a}`;
}

function diasEntre(desdeIso: string, hastaIso: string): number {
  return Math.round((new Date(`${hastaIso}T12:00:00`).getTime() - new Date(`${desdeIso}T12:00:00`).getTime()) / 86400000);
}

const CODIGOS_TIPO = [...TIPOS_VENTA, ...TIPOS_OTROS].map((t) => t.code);
const ES_FECHA = /^\d{4}-\d{2}-\d{2}$/;

/** Mensaje legible de un error de erp-api (los 422 de FastAPI traen una lista). */
function mensajeErp(err: unknown): string {
  if (!(err instanceof ErpApiError)) return "el servicio de clientes y cotizaciones no responde";
  try {
    const lista = JSON.parse(err.message) as { msg?: string }[];
    if (Array.isArray(lista)) return lista.map((e) => e.msg).filter(Boolean).join(" · ") || err.message;
  } catch {
    // no era JSON
  }
  return err.message;
}

/** Origen del formulario cuando se abre desde otra pantalla (cotización, copia de una rechazada, nota). */
type Origen =
  | { tipo: "cotizacion"; cotizacion: Cotizacion }
  | { tipo: "copia"; encf: string | null; estado: string }
  | { tipo: "nota"; encf: string; fecha: string };

function porcentajeDescuento(item: ItemFactura): string {
  const bruto = (Number(item.CantidadItem) || 0) * (Number(item.PrecioUnitarioItem) || 0);
  const desc = Number(item.DescuentoMonto) || 0;
  if (!bruto || !desc) return "";
  return String(Math.round((desc / bruto) * 10000) / 100);
}

/** Cantidades sin ceros sobrantes: "1250.0000" -> "1,250". */
function cantidadLegible(valor: number | string): string {
  return Number(valor).toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function IconoInfo() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 8h.01" />
    </svg>
  );
}

function IconoAlerta() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </svg>
  );
}

export function FacturaForm() {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const editando = Boolean(id);
  const { facturas, recargar } = useFacturas();

  // Parámetros de /facturas/nueva: ?tipo=34&afecta=E31…&fecha=YYYY-MM-DD, ?cotizacion=<id>, ?cliente=<id>, ?copiar=<id>
  const [params] = useSearchParams();
  const param = (clave: string) => (editando ? null : params.get(clave));
  const tipoParam = param("tipo");
  const afectaParam = param("afecta")?.trim().toUpperCase().slice(0, 13) || null;
  const fechaParam = param("fecha");
  const cotizacionId = param("cotizacion");
  const clienteId = param("cliente");
  const copiarId = param("copiar");
  const tipoInicial = tipoParam && CODIGOS_TIPO.includes(tipoParam) ? tipoParam : "31";

  const [idGuardado, setIdGuardado] = useState<string | null>(id ?? null);
  const [tipoEcf, setTipoEcf] = useState(tipoInicial);
  const [form, setForm] = useState<FormState>(() => {
    const inicial = { ...estadoInicial(tipoInicial), fechaVencimientoSecuencia: leerVencimientoSecuencia(tipoInicial) };
    if (!afectaParam) return inicial;
    const fecha = fechaParam && ES_FECHA.test(fechaParam) ? fechaParam : "";
    return {
      ...inicial,
      ncfModificado: afectaParam,
      fechaNcfModificado: fecha,
      // Nota de crédito: la DGII pide indicar si pasaron más de 30 días desde la factura original.
      indicadorNotaCredito: fecha && diasEntre(fecha, inicial.fechaEmision) > 30 ? "1" : "0",
    };
  });
  const [encfManual, setEncfManual] = useState(false);
  const [formaPago, setFormaPago] = useState("1");
  const [emisor, setEmisor] = useState<DatosEmisor>(leerEmisor);
  const [editandoEmisor, setEditandoEmisor] = useState(() => !leerEmisor().direccion);
  /** El emisor viene de Configuración → Datos de la empresa (si no, del navegador). */
  const [emisorDeConfiguracion, setEmisorDeConfiguracion] = useState(false);
  /** Secuencias e-NCF de Configuración por tipo (null = erp-api no respondió: respaldo local). */
  const [secuencias, setSecuencias] = useState<Record<string, Secuencia> | null>(null);
  const [vendedores, setVendedores] = useState<Vendedor[]>([]);
  const [vendedorId, setVendedorId] = useState("");
  const [consumidorFinal, setConsumidorFinal] = useState(true);
  const [clienteEstado, setClienteEstado] = useState<"" | "buscando" | "encontrado" | "nuevo">("");
  /** Contacto de erp-api elegido para esta factura (null si es nuevo o no se pudo consultar). */
  const [clienteErp, setClienteErp] = useState<Cliente | null>(null);
  const [contactosSinConexion, setContactosSinConexion] = useState(false);
  const [sugerencias, setSugerencias] = useState<Cliente[]>([]);
  const [origen, setOrigen] = useState<Origen | null>(() =>
    afectaParam ? { tipo: "nota", encf: afectaParam, fecha: fechaParam && ES_FECHA.test(fechaParam) ? fechaParam : "" } : null,
  );
  const [avisoForm, setAvisoForm] = useState<string | null>(null);
  const cotizacionMarcada = useRef(false);
  const avisoAlSalir = useRef<string | null>(null);
  const compradorDeOriginal = useRef(false);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  /** Existencia de cada producto en el almacén elegido (null = no se pudo consultar: se usa el total). */
  const [existencias, setExistencias] = useState<Map<string, number> | null>(null);
  /** Lotes con existencia por "producto|almacén" (productos que manejan lotes). */
  const [lotes, setLotes] = useState<Record<string, Lote[]>>({});
  /** Sugerencia FEFO por "producto|almacén|cantidad" para las líneas sin lote elegido. */
  const [fefo, setFefo] = useState<Record<string, SugerenciaFefo>>({});
  const [facturaCompleta, setFacturaCompleta] = useState<FacturaDetalle | null>(null);
  const [cargando, setCargando] = useState(editando || Boolean(cotizacionId || copiarId));
  const [procesando, setProcesando] = useState<"" | "borrador" | "emitir">("");
  const [confirmar, setConfirmar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [erroresXml, setErroresXml] = useState<string[]>([]);

  const config = useMemo(() => configParaTipo(tipoEcf), [tipoEcf]);
  const totales = useMemo(() => desglose(form.items), [form.items]);
  const tipoActual = [...TIPOS_VENTA, ...TIPOS_OTROS].find((t) => t.code === tipoEcf);
  const esNota = tipoEcf === "33" || tipoEcf === "34";
  const esConsumo = tipoEcf === "32";
  const exigeIdentificar = esConsumo && totales.total >= TOPE_CONSUMO_ANONIMO;
  const pideComprador = config.compradorVisible && (!esConsumo || !consumidorFinal || exigeIdentificar);

  useEffect(() => {
    inventarioApi
      .listarProductos({ activo: true })
      .then(setProductos)
      .catch(() => setProductos([]));
    inventarioApi
      .listarAlmacenes({ activo: true })
      .then(setAlmacenes)
      .catch(() => setAlmacenes([]));
  }, []);

  // Configuración (erp-api): emisor, secuencias e-NCF y vendedores. Si no
  // responde, el formulario sigue con lo recordado en el navegador.
  useEffect(() => {
    configuracionApi
      .empresa()
      .then((e) => {
        publicarEmpresa(e);
        if (!e.configurada || editando) return;
        setEmisor({ rnc: e.rnc ?? "", razonSocial: e.razon_social, direccion: e.direccion ?? "", correo: e.correo ?? "" });
        setEditandoEmisor(!e.direccion);
        setEmisorDeConfiguracion(true);
      })
      .catch(() => undefined);
    configuracionApi
      .secuencias()
      .then((l) => setSecuencias(Object.fromEntries(l.map((x) => [String(x.tipo_ecf), x]))))
      .catch(() => setSecuencias(null));
    configuracionApi
      .vendedores({ activo: true })
      .then(setVendedores)
      .catch(() => setVendedores([]));
  }, [editando]);

  const secuencia = secuencias?.[tipoEcf]?.configurada ? secuencias[tipoEcf] : null;

  // La fecha de vencimiento de la secuencia sale de Configuración cuando está registrada.
  useEffect(() => {
    if (!secuencia?.fecha_vencimiento || editando) return;
    setForm((prev) =>
      prev.fechaVencimientoSecuencia === secuencia.fecha_vencimiento ? prev : { ...prev, fechaVencimientoSecuencia: secuencia.fecha_vencimiento ?? "" },
    );
  }, [secuencia, editando]);

  /** Almacén de la factura: el elegido o, si no se eligió, el principal. */
  const almacenId = form.almacenId || almacenes.find((a) => a.es_principal)?.id || "";
  const almacen = almacenes.find((a) => a.id === almacenId);

  useEffect(() => {
    if (!almacenId) return setExistencias(null);
    inventarioApi
      .existenciasAlmacen(almacenId, { incluir_cero: true })
      .then((ex) => setExistencias(new Map(ex.map((e) => [e.producto_id, Number(e.cantidad)]))))
      .catch(() => setExistencias(null));
  }, [almacenId]);

  // Lotes del almacén de los productos con lotes que aparecen en las líneas.
  useEffect(() => {
    if (!almacenId) return;
    const faltan = [...new Set(form.items.map((i) => i.productoId))].filter(
      (pid): pid is string => Boolean(pid) && Boolean(productos.find((p) => p.id === pid)?.maneja_lotes) && !lotes[`${pid}|${almacenId}`],
    );
    if (faltan.length === 0) return;
    setLotes((prev) => ({ ...prev, ...Object.fromEntries(faltan.map((pid) => [`${pid}|${almacenId}`, []])) }));
    for (const pid of faltan) {
      inventarioApi
        .listarLotes({ producto_id: pid, almacen_id: almacenId, con_existencia: true })
        .then((ls) => setLotes((prev) => ({ ...prev, [`${pid}|${almacenId}`]: ls })))
        .catch(() => undefined);
    }
  }, [form.items, productos, almacenId, lotes]);

  // Sugerencia FEFO (de qué lotes saldría la venta) de las líneas con lotes sin lote elegido.
  const clavesFefo = form.items
    .filter((i) => i.productoId && !i.loteId && Number(i.CantidadItem) > 0 && productos.find((p) => p.id === i.productoId)?.maneja_lotes)
    .map((i) => `${i.productoId}|${almacenId}|${Number(i.CantidadItem)}`)
    .filter((k) => !fefo[k])
    .join(",");
  useEffect(() => {
    if (!clavesFefo || !almacenId) return;
    const t = window.setTimeout(() => {
      for (const clave of clavesFefo.split(",")) {
        const [producto_id, almacen_id, cantidad] = clave.split("|");
        inventarioApi
          .sugerirFefo({ producto_id, almacen_id, cantidad })
          .then((s) => setFefo((prev) => ({ ...prev, [clave]: s })))
          .catch(() => undefined);
      }
    }, 300);
    return () => window.clearTimeout(t);
  }, [clavesFefo, almacenId]);

  useEffect(() => {
    if (!id) return;
    setCargando(true);
    api
      .obtener(id)
      .then((factura) => {
        setFacturaCompleta(factura);
        setVendedorId(factura.vendedor_id ?? "");
        if (factura.estado !== "borrador") {
          setError(`Solo se pueden editar facturas en borrador (esta está "${factura.estado}").`);
          return;
        }
        const tipo = String(factura.tipo_ecf);
        const estado = datosAFormState(tipo, factura.encf ?? "", factura.datos, factura.items_inventario);
        setTipoEcf(tipo);
        setForm(estado);
        setEncfManual(true);
        setFormaPago(estado.formasPago[0]?.FormaPago ?? (estado.tipoPago === "2" ? "4" : "1"));
        setConsumidorFinal(!estado.compradorRnc && !estado.razonSocialComprador);
        if (estado.rncEmisor) {
          setEmisor({
            rnc: estado.rncEmisor,
            razonSocial: estado.razonSocialEmisor,
            direccion: estado.direccionEmisor,
            correo: estado.correoEmisor,
          });
          setEditandoEmisor(false);
        }
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setCargando(false));
  }, [id]);

  /** Llena el comprador con un contacto de erp-api y aplica su condición de pago. */
  function aplicarCliente(c: Cliente) {
    const pasaporte = c.tipo_identificacion === "pasaporte";
    setClienteErp(c);
    setClienteEstado("encontrado");
    setConsumidorFinal(false);
    setForm((prev) => ({
      ...prev,
      compradorRnc: pasaporte ? "" : (c.rnc ?? prev.compradorRnc),
      compradorIdentificadorExtranjero: pasaporte ? (c.rnc ?? "") : prev.compradorIdentificadorExtranjero,
      razonSocialComprador: c.nombre,
      correoComprador: c.correo ?? "",
      direccionComprador: c.direccion ?? "",
      ...(c.condicion_pago_dias > 0 ? { fechaLimitePago: sumarDias(prev.fechaEmision, c.condicion_pago_dias) } : {}),
    }));
    if (c.condicion_pago_dias > 0) setFormaPago("4");
  }

  function aplicarTipo(nuevo: string) {
    setTipoEcf(nuevo);
    setEncfManual(false);
    setForm((prev) => ({ ...prev, tipoEcf: nuevo, fechaVencimientoSecuencia: leerVencimientoSecuencia(nuevo) }));
  }

  // ?cotizacion=<id>: cliente y líneas de la cotización.
  useEffect(() => {
    if (!cotizacionId) return;
    erpApi
      .obtenerCotizacion(cotizacionId)
      .then(async (cot) => {
        setOrigen({ tipo: "cotizacion", cotizacion: cot });
        if (cot.estado === "facturada") cotizacionMarcada.current = true;
        if (!cot.cliente_rnc && !tipoParam) aplicarTipo("32");
        setConsumidorFinal(!cot.cliente_rnc && !cot.cliente_nombre);
        setForm((prev) => ({
          ...prev,
          compradorRnc: cot.cliente_rnc ?? "",
          razonSocialComprador: cot.cliente_nombre ?? "",
          items: cot.items.length > 0 ? itemsDesdeCotizacion(cot.items) : prev.items,
        }));
        // Correo, dirección y condición de pago salen del contacto (si erp-api lo tiene).
        try {
          if (cot.cliente_id) aplicarCliente(await erpApi.obtenerCliente(cot.cliente_id));
          else if (cot.cliente_rnc) aplicarCliente(await erpApi.clientePorRnc(cot.cliente_rnc));
        } catch {
          // sin contacto: se queda con el nombre y el RNC de la cotización
        }
      })
      .catch((err) => setAvisoForm(`No se pudo cargar la cotización: ${mensajeErp(err)}. Puede llenar la factura a mano.`))
      .finally(() => setCargando(false));
  }, [cotizacionId]);

  // ?cliente=<id>: "Facturar" desde Contactos.
  useEffect(() => {
    if (!clienteId) return;
    erpApi
      .obtenerCliente(clienteId)
      .then((c) => {
        if (!c.rnc && !tipoParam) aplicarTipo("32");
        aplicarCliente(c);
      })
      .catch((err) => setAvisoForm(`No se pudo cargar el contacto: ${mensajeErp(err)}.`));
  }, [clienteId]);

  // ?copiar=<id>: "Corregir y emitir nueva factura" desde una rechazada. Mismos datos, e-NCF nuevo.
  useEffect(() => {
    if (!copiarId) return;
    api
      .obtener(copiarId)
      .then((original) => {
        const tipo = String(original.tipo_ecf);
        const copia = datosAFormState(tipo, "", original.datos, original.items_inventario);
        const hoy = estadoInicial(tipo).fechaEmision;
        const plazo = copia.fechaLimitePago ? diasEntre(copia.fechaEmision, copia.fechaLimitePago) : 0;
        setTipoEcf(tipo);
        setEncfManual(false);
        setForm({
          ...copia,
          encf: "",
          fechaEmision: hoy,
          fechaLimitePago: plazo > 0 ? sumarDias(hoy, plazo) : copia.fechaLimitePago,
          fechaVencimientoSecuencia: copia.fechaVencimientoSecuencia || leerVencimientoSecuencia(tipo),
        });
        setFormaPago(copia.formasPago[0]?.FormaPago ?? (copia.tipoPago === "2" ? "4" : "1"));
        setConsumidorFinal(!copia.compradorRnc && !copia.razonSocialComprador);
        setOrigen({ tipo: "copia", encf: original.encf, estado: original.estado });
        setVendedorId(original.vendedor_id ?? "");
      })
      .catch((err) => setAvisoForm(`No se pudo copiar la factura: ${err instanceof ApiError ? err.message : String(err)}.`))
      .finally(() => setCargando(false));
  }, [copiarId]);

  // ?afecta=<e-NCF>: la nota va al mismo comprador de la factura original.
  useEffect(() => {
    if (!afectaParam || compradorDeOriginal.current) return;
    const original = facturas.find((f) => f.encf === afectaParam);
    if (!original) return;
    compradorDeOriginal.current = true;
    api
      .obtener(original.id)
      .then((fac) => {
        const c = (fac.datos.Encabezado.Comprador ?? {}) as Record<string, unknown>;
        if (!c.RNCComprador && !c.RazonSocialComprador) return;
        setConsumidorFinal(false);
        setForm((prev) =>
          prev.compradorRnc || prev.razonSocialComprador
            ? prev
            : {
                ...prev,
                compradorRnc: String(c.RNCComprador ?? ""),
                razonSocialComprador: String(c.RazonSocialComprador ?? ""),
                correoComprador: String(c.CorreoComprador ?? ""),
                direccionComprador: String(c.DireccionComprador ?? ""),
              },
        );
      })
      .catch(() => undefined);
  }, [afectaParam, facturas]);

  // Búsqueda de clientes por nombre mientras se escribe la razón social.
  const nombreBuscado = clienteEstado === "encontrado" ? "" : form.razonSocialComprador.trim();
  useEffect(() => {
    if (nombreBuscado.length < 2) {
      setSugerencias([]);
      return;
    }
    const t = window.setTimeout(() => {
      erpApi
        .listarClientes({ q: nombreBuscado, activo: true })
        .then(setSugerencias)
        .catch(() => setSugerencias([]));
    }, 300);
    return () => window.clearTimeout(t);
  }, [nombreBuscado]);

  /** Próximo e-NCF sugerido: el de la secuencia de Configuración o, si no hay, el mayor ya usado de este tipo + 1. */
  const proximoEncf = useMemo(() => {
    const local = siguienteEncf(facturas, tipoEcf);
    const deSecuencia = secuencia?.siguiente_encf;
    return deSecuencia && deSecuencia > local ? deSecuencia : local;
  }, [facturas, tipoEcf, secuencia]);
  const secuenciaBloquea = secuencia && (secuencia.estado === "vencida" || secuencia.estado === "agotada" || secuencia.estado === "inactiva");

  // También se reaplica cuando algo deja el e-NCF vacío (p. ej. ?copiar=, que copia los datos con e-NCF nuevo).
  useEffect(() => {
    if (!encfManual) setForm((prev) => (prev.encf === proximoEncf ? prev : { ...prev, encf: proximoEncf }));
  }, [proximoEncf, encfManual, form.encf]);

  const facturasReferenciables = useMemo(
    () => facturas.filter((f) => f.encf && (f.estado === "aceptado" || f.estado === "enviado")),
    [facturas],
  );

  function set<K extends keyof FormState>(campo: K, valor: FormState[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
  }

  function cambiarTipo(nuevo: string) {
    if (editando || nuevo === tipoEcf) return;
    aplicarTipo(nuevo);
  }

  /** "Verificar": busca el RNC en Contactos (erp-api). Si el servicio no responde, usa lo recordado en el navegador. */
  async function verificarCliente() {
    const rnc = form.compradorRnc.replace(/\D/g, "");
    if (!rnc) return;
    setClienteEstado("buscando");
    try {
      aplicarCliente(await erpApi.clientePorRnc(rnc));
      setContactosSinConexion(false);
    } catch (err) {
      setClienteErp(null);
      if (err instanceof ErpApiError && err.status === 404) {
        setContactosSinConexion(false);
        setClienteEstado("nuevo");
        return;
      }
      setContactosSinConexion(true);
      const encontrado = buscarCliente(rnc);
      if (encontrado) {
        setForm((prev) => ({
          ...prev,
          razonSocialComprador: encontrado.razonSocial,
          correoComprador: encontrado.correo,
          direccionComprador: encontrado.direccion,
        }));
        setClienteEstado("encontrado");
      } else {
        setClienteEstado("nuevo");
      }
    }
  }

  /** Al guardar con un RNC que no está en Contactos, lo agrega. Nunca bloquea ni hace fallar la factura. */
  async function registrarContacto() {
    if (!pideComprador || clienteErp) return;
    const rnc = form.compradorRnc.replace(/\D/g, "");
    const tipoId = rnc.length === 9 ? "rnc" : rnc.length === 11 ? "cedula" : null;
    if (!tipoId || !form.razonSocialComprador.trim()) return;
    try {
      setClienteErp(await erpApi.clientePorRnc(rnc));
    } catch (err) {
      if (!(err instanceof ErpApiError && err.status === 404)) return;
      try {
        const creado = await erpApi.crearCliente({
          tipo: tipoEcf === "41" || tipoEcf === "47" ? "proveedor" : "cliente",
          tipo_identificacion: tipoId,
          rnc,
          nombre: form.razonSocialComprador.trim(),
          correo: form.correoComprador.trim() || null,
          direccion: form.direccionComprador.trim() || null,
        });
        setClienteErp(creado);
      } catch {
        // erp-api caído o rechazó el contacto: la factura ya se guardó, no se interrumpe
      }
    }
  }

  /** Vincula la cotización de origen con la factura recién creada. Devuelve un aviso si falla. */
  async function marcarCotizacion(facturaId: string): Promise<string | null> {
    if (origen?.tipo !== "cotizacion" || cotizacionMarcada.current) return null;
    try {
      await erpApi.marcarCotizacionFacturada(origen.cotizacion.id, facturaId);
      cotizacionMarcada.current = true;
      return null;
    } catch (err) {
      return `La factura se guardó, pero la cotización ${origen.cotizacion.numero} no se pudo marcar como facturada (${mensajeErp(err)}). Márquela desde Cotizaciones.`;
    }
  }

  function actualizarItem(index: number, cambios: Partial<ItemFormulario>) {
    setForm((prev) => {
      const items = prev.items.slice();
      items[index] = { ...items[index], ...cambios };
      return { ...prev, items };
    });
  }

  function elegirProducto(index: number, productoId: string) {
    const producto = productos.find((p) => p.id === productoId);
    if (!producto) {
      actualizarItem(index, { productoId: undefined, loteId: undefined, NombreItem: "", IndicadorBienoServicio: "2" });
      return;
    }
    actualizarItem(index, {
      productoId: producto.id,
      loteId: undefined,
      NombreItem: producto.nombre,
      PrecioUnitarioItem: producto.precio_venta,
      IndicadorBienoServicio: producto.tipo === "servicio" ? "2" : "1",
      IndicadorFacturacion: INDICADOR_POR_TASA[producto.tasa_itbis] ?? "1",
    });
  }

  function cambiarAlmacen(id: string) {
    // los lotes son de cada almacén: al cambiarlo, las líneas vuelven a FEFO
    setForm((prev) => ({ ...prev, almacenId: id, items: prev.items.map((i) => (i.loteId ? { ...i, loteId: undefined } : i)) }));
  }

  /** Existencia disponible de una línea en el almacén de la factura (o en su lote); null = sin inventario. */
  function disponibleDe(item: ItemFormulario): number | null {
    const producto = productos.find((p) => p.id === item.productoId);
    if (!producto || producto.tipo === "servicio") return null;
    if (item.loteId) {
      const lote = (lotes[`${producto.id}|${almacenId}`] ?? []).find((l) => l.id === item.loteId);
      if (lote) return Number(lote.existencias.find((e) => e.almacen_id === almacenId)?.cantidad ?? 0);
    }
    if (!existencias) return Number(producto.stock_actual);
    return existencias.get(producto.id) ?? 0;
  }

  function cambiarDescuento(index: number, porcentaje: string) {
    const item = form.items[index];
    const bruto = (Number(item.CantidadItem) || 0) * (Number(item.PrecioUnitarioItem) || 0);
    const pct = Number(porcentaje) || 0;
    actualizarItem(index, { DescuentoMonto: pct > 0 ? decimal2((bruto * pct) / 100) : undefined });
  }

  function cambiarCantidadOPrecio(index: number, cambios: Partial<ItemFactura>) {
    // el descuento se guarda como monto: se recalcula para mantener el mismo porcentaje
    const item = form.items[index];
    const pct = Number(porcentajeDescuento(item)) || 0;
    const nuevo = { ...item, ...cambios };
    const bruto = (Number(nuevo.CantidadItem) || 0) * (Number(nuevo.PrecioUnitarioItem) || 0);
    actualizarItem(index, { ...cambios, DescuentoMonto: pct > 0 ? decimal2((bruto * pct) / 100) : nuevo.DescuentoMonto });
  }

  function agregarLinea() {
    setForm((prev) => ({ ...prev, items: [...prev.items, nuevoItem(prev.items.length + 1)] }));
  }

  function quitarLinea(index: number) {
    setForm((prev) => (prev.items.length <= 1 ? prev : { ...prev, items: prev.items.filter((_, i) => i !== index) }));
  }

  function validar(): string | null {
    if (!emisor.rnc || !emisor.razonSocial || !emisor.direccion) return "Complete los datos del emisor (RNC, razón social y dirección).";
    if (!form.encf || form.encf.length !== 13) return "El e-NCF debe tener 13 caracteres (por ejemplo E310000000001).";
    if (secuenciaBloquea && !editando)
      return `La secuencia E${tipoEcf} está ${secuencia?.estado === "inactiva" ? "desactivada" : secuencia?.estado}: actualícela en Configuración → Secuencias autorizadas.`;
    if (secuencia?.hasta != null && Number(form.encf.slice(3)) > secuencia.hasta)
      return `El e-NCF ${form.encf} está fuera del rango autorizado (hasta ${secuencia.prefijo}${String(secuencia.hasta).padStart(10, "0")}).`;
    if (config.requiereFechaVencimientoSecuencia && !form.fechaVencimientoSecuencia)
      return "Indique hasta cuándo está autorizada la secuencia de este tipo de comprobante.";
    if (pideComprador && config.compradorRncRequerido && !form.compradorRnc) return "El RNC o cédula del cliente es obligatorio para este tipo de comprobante.";
    if (pideComprador && (form.compradorRnc || config.compradorRncRequerido) && !form.razonSocialComprador)
      return "Escriba la razón social del cliente.";
    if (exigeIdentificar && !form.compradorRnc) return "El total supera RD$ 250,000.00: identifique al comprador con RNC o cédula.";
    if (config.informacionReferenciaRequerida && (!form.ncfModificado || !form.fechaNcfModificado))
      return "Indique el e-NCF original que modifica y su fecha.";
    if (form.items.some((i) => !i.NombreItem)) return "Cada línea debe tener un producto o una descripción.";
    if (form.items.some((i) => !(Number(i.CantidadItem) > 0))) return "Las cantidades deben ser mayores que cero.";
    if (formaPago === "4" && !form.fechaLimitePago) return "Indique el vencimiento del pago para la venta a crédito.";
    return null;
  }

  /** Guarda (crea o actualiza) la factura y devuelve su id. */
  async function guardar(): Promise<string> {
    const tipoPago = formaPago === "4" ? "2" : "1";
    const estado: FormState = {
      ...form,
      tipoEcf,
      tipoPago,
      formasPago: [{ FormaPago: formaPago, MontoPago: decimal2(totales.total) }],
      rncEmisor: emisor.rnc.replace(/\D/g, ""),
      razonSocialEmisor: emisor.razonSocial,
      direccionEmisor: emisor.direccion,
      correoEmisor: emisor.correo,
      ...(pideComprador
        ? {}
        : { compradorRnc: "", compradorIdentificadorExtranjero: "", razonSocialComprador: "", correoComprador: "", direccionComprador: "" }),
    };
    const datos = construirDatos(estado, config);
    const items_inventario = construirItemsInventario(estado.items, almacenId);
    guardarEmisor(emisor);
    guardarVencimientoSecuencia(tipoEcf, form.fechaVencimientoSecuencia);
    if (pideComprador) {
      guardarCliente(form.compradorRnc, {
        razonSocial: form.razonSocialComprador,
        correo: form.correoComprador,
        direccion: form.direccionComprador,
      });
    }
    if (idGuardado) {
      await api.actualizar(idGuardado, { encf: form.encf || null, datos, items_inventario, vendedor_id: vendedorId || null });
      void registrarContacto();
      return idGuardado;
    }
    const creada = await api.crear({
      tipo_ecf: Number(tipoEcf),
      encf: form.encf || null,
      datos,
      items_inventario,
      vendedor_id: vendedorId || null,
    });
    setIdGuardado(creada.id);
    void registrarContacto();
    return creada.id;
  }

  /** Estado de navegación hacia el detalle: lleva el aviso de la cotización si quedó alguno. */
  function estadoAlSalir(contingencia?: string | null) {
    const detalle = avisoAlSalir.current;
    if (contingencia) {
      // Emitida en contingencia: el aviso del detalle explica que se enviará sola.
      return { state: { aviso: { texto: "Emitida en contingencia", detalle: detalle ? `${contingencia} ${detalle}` : contingencia, tono: "aviso" } } };
    }
    return detalle ? { state: { aviso: { texto: "Cotización sin marcar como facturada", detalle, tono: "aviso" } } } : undefined;
  }

  async function guardarBorrador() {
    setError(null);
    setErroresXml([]);
    const problema = validar();
    if (problema) return setError(problema);
    setProcesando("borrador");
    try {
      const fid = await guardar();
      recargar();
      navigate(`/facturas/${fid}`, estadoAlSalir());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando("");
    }
  }

  function pedirConfirmacion() {
    setError(null);
    setErroresXml([]);
    const problema = validar();
    if (problema) return setError(problema);
    setConfirmar(true);
  }

  async function emitir() {
    setConfirmar(false);
    setProcesando("emitir");
    try {
      const fid = await guardar();
      const xml = await api.generarXml(fid);
      if (xml.errores.length > 0) {
        setErroresXml(xml.errores);
        setError("La DGII no aceptaría este comprobante. Corrija lo siguiente y vuelva a emitir (el borrador quedó guardado).");
        return;
      }
      const envio = await api.enviar(fid);
      // La cotización se marca facturada solo cuando la factura quedó emitida
      // (llegó a la DGII o quedó firmada en contingencia): un borrador se puede
      // borrar y dejaría la cotización atada a nada.
      if (envio.track_id || envio.contingencia) {
        const aviso = await marcarCotizacion(fid);
        avisoAlSalir.current = aviso;
      }
      recargar();
      navigate(`/facturas/${fid}`, estadoAlSalir(envio.contingencia ? (envio.mensaje ?? MENSAJE_CONTINGENCIA) : null));
    } catch (err) {
      recargar();
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setProcesando("");
    }
  }

  if (cargando) return <div className="content"><p className="mut">Cargando…</p></div>;

  const faltaStock = form.items.some((item) => {
    const disponible = disponibleDe(item);
    return disponible != null && disponible < (Number(item.CantidadItem) || 0);
  });

  return (
    <div className="content">
      <div className="ph">
        <div>
          <div className="crumb">
            <Link to="/facturas">Facturas de venta</Link>
            <span>/</span>
            <span>{editando ? "Editar borrador" : "Nueva"}</span>
          </div>
          <h1>{editando ? "Editar factura" : "Nueva factura electrónica"}</h1>
          <p>
            Próximo e-NCF:{" "}
            <span className="mono" style={{ color: "#111827", fontWeight: 600 }}>
              {form.encf || proximoEncf}
            </span>
            {form.fechaVencimientoSecuencia && ` · Secuencia autorizada hasta ${fechaLegible(form.fechaVencimientoSecuencia)}`}
          </p>
        </div>
      </div>

      {origen?.tipo === "cotizacion" && (
        <div className={origen.cotizacion.estado === "facturada" ? "al al-w" : "al al-i"}>
          <IconoInfo />
          <span>
            {origen.cotizacion.estado === "facturada" ? (
              <>
                <b>La cotización {origen.cotizacion.numero} ya fue facturada.</b> Si continúa, se emitirá otra factura con los mismos datos.
              </>
            ) : (
              <>
                <b>Factura a partir de la cotización {origen.cotizacion.numero}</b> de {origen.cotizacion.cliente_nombre} por RD${" "}
                {formatoMonto(origen.cotizacion.total)}. Al emitirla, la cotización quedará marcada como facturada.
              </>
            )}
          </span>
        </div>
      )}
      {origen?.tipo === "copia" && (
        <div className="al al-i">
          <IconoInfo />
          <span>
            <b>Copia de {origen.encf ?? "la factura"}{origen.estado === "rechazado" ? " (rechazada por la DGII)" : ""}.</b> Corrija los datos antes de
            emitir: se usará un e-NCF nuevo, porque el anterior no puede reutilizarse.
          </span>
        </div>
      )}
      {origen?.tipo === "nota" && (
        <div className="al al-i">
          <IconoInfo />
          <span>
            <b>
              {tipoEcf === "33" ? "Nota de débito" : tipoEcf === "34" ? "Nota de crédito" : "Comprobante"} que modifica{" "}
              <span className="mono">{origen.encf}</span>
              {origen.fecha && ` del ${fechaLegible(origen.fecha)}`}.
            </b>{" "}
            Indique las líneas y montos que {tipoEcf === "33" ? "aumentan" : "anulan o reducen"} la factura original.
          </span>
        </div>
      )}
      {secuencia && secuencia.avisos.length > 0 && (
        <div className={secuenciaBloquea ? "al al-d" : "al al-w"} role="status">
          <IconoAlerta />
          <span>
            <b>Secuencia E{tipoEcf}:</b> {secuencia.avisos.join(" ")}{" "}
            <Link to="/configuracion/secuencias">Ir a Secuencias autorizadas</Link>
          </span>
        </div>
      )}
      {secuencias && !secuencia && !editando && (
        <div className="al al-i" role="status">
          <IconoInfo />
          <span>
            La secuencia E{tipoEcf} no está registrada en Configuración: se propone el siguiente e-NCF a los ya usados.{" "}
            <Link to="/configuracion/secuencias">Registrar el rango autorizado</Link>
          </span>
        </div>
      )}
      {avisoForm && (
        <div className="al al-w" role="status">
          <IconoAlerta />
          <span>{avisoForm}</span>
        </div>
      )}

      <section className="card">
        <div className="card-h">
          <h2>Emisor</h2>
          {!editandoEmisor && (
            <button type="button" className="btn btn-g btn-sm" onClick={() => setEditandoEmisor(true)}>
              Cambiar
            </button>
          )}
        </div>
        <div className="card-b">
          {editandoEmisor ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
              <div className="fld">
                <label className="lbl" htmlFor="em-rnc">RNC emisor <span className="req">*</span></label>
                <input id="em-rnc" className="inp mono" value={emisor.rnc} onChange={(e) => setEmisor({ ...emisor, rnc: e.target.value })} placeholder="131897542" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="em-rs">Razón social <span className="req">*</span></label>
                <input id="em-rs" className="inp" value={emisor.razonSocial} onChange={(e) => setEmisor({ ...emisor, razonSocial: e.target.value })} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="em-dir">Dirección <span className="req">*</span></label>
                <input id="em-dir" className="inp" value={emisor.direccion} onChange={(e) => setEmisor({ ...emisor, direccion: e.target.value })} placeholder="Calle, número, sector, ciudad" />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="em-mail">Correo</label>
                <input id="em-mail" type="email" className="inp" value={emisor.correo} onChange={(e) => setEmisor({ ...emisor, correo: e.target.value })} />
              </div>
              <span className="hint" style={{ gridColumn: "1 / -1" }}>
                {emisorDeConfiguracion ? (
                  <>
                    Vienen de <Link to="/configuracion/empresa">Configuración → Datos de la empresa</Link>; los cambios aquí solo aplican a esta factura.
                  </>
                ) : (
                  <>
                    Se recuerdan en este navegador. Guárdelos en <Link to="/configuracion/empresa">Configuración</Link> para todas las facturas.
                  </>
                )}
              </span>
            </div>
          ) : (
            <div className="row" style={{ gap: 16 }}>
              <b style={{ color: "#111827" }}>{emisor.razonSocial}</b>
              <span className="mono mut">RNC {formatoRnc(emisor.rnc)}</span>
              <span className="mut">{emisor.direccion}</span>
            </div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-h">
          <h2>Tipo de comprobante</h2>
          <span className="hint">El formulario se adapta a los requisitos de cada tipo de e-CF</span>
        </div>
        <div className="card-b stack-sm">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
            {TIPOS_VENTA.map((t) => (
              <TarjetaTipo key={t.code} tipo={t} activo={t.code === tipoEcf} deshabilitado={editando} onElegir={cambiarTipo} />
            ))}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <span className="hint">Otros comprobantes:</span>
            {TIPOS_OTROS.map((t) => (
              <button
                key={t.code}
                type="button"
                className={`btn btn-sm ${t.code === tipoEcf ? "btn-p" : "btn-g"}`}
                onClick={() => cambiarTipo(t.code)}
                disabled={editando}
                title={t.desc}
              >
                E{t.code} · {t.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 620px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="card-h">
              <h2>{tipoEcf === "41" || tipoEcf === "47" ? "Proveedor" : "Cliente"}</h2>
              <span className="chip">
                {!config.compradorVisible ? "No lleva comprador" : config.compradorRncRequerido ? "RNC obligatorio" : "Cliente opcional"}
              </span>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {esConsumo && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <button
                      type="button"
                      className={consumidorFinal ? "sw on" : "sw"}
                      onClick={() => setConsumidorFinal(!consumidorFinal)}
                      aria-pressed={consumidorFinal}
                      aria-label="Usar cliente genérico"
                    />
                    <div>
                      <div style={{ fontWeight: 600, color: "#111827" }}>Consumidor final (cliente genérico)</div>
                      <div className="hint">No requiere RNC ni cédula para montos menores de RD$ 250,000.00</div>
                    </div>
                  </div>
                  {exigeIdentificar && (
                    <div className="al al-w">
                      <IconoAlerta />
                      <span>
                        <b>El total supera RD$ 250,000.00.</b> Identifique al comprador con RNC o cédula para poder emitir el e-CF de Consumo.
                      </span>
                    </div>
                  )}
                </div>
              )}

              {pideComprador && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
                  <div className="fld">
                    <label className="lbl" htmlFor="rnc">
                      RNC o cédula {tipoEcf === "41" || tipoEcf === "47" ? "del proveedor" : "del cliente"}
                      {config.compradorRncRequerido && <span className="req">*</span>}
                      <span className="tip" data-tip="Registro Nacional del Contribuyente (9 dígitos) o cédula (11 dígitos).">
                        <button type="button" className="info" aria-label="Qué es el RNC">
                          <IconoInfo />
                        </button>
                      </span>
                    </label>
                    <div style={{ display: "flex", gap: 8 }}>
                      <input
                        id="rnc"
                        className="inp mono"
                        value={form.compradorRnc}
                        onChange={(e) => {
                          set("compradorRnc", e.target.value.replace(/[^\d-]/g, ""));
                          setClienteEstado("");
                          setClienteErp(null);
                        }}
                        onBlur={() => form.compradorRnc && !form.razonSocialComprador && verificarCliente()}
                        placeholder="130452187"
                        inputMode="numeric"
                      />
                      <button type="button" className="btn btn-o" onClick={verificarCliente} disabled={!form.compradorRnc || clienteEstado === "buscando"}>
                        {clienteEstado === "buscando" ? "Buscando…" : "Verificar"}
                      </button>
                    </div>
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="rs">Razón social</label>
                    {clienteEstado === "encontrado" && form.razonSocialComprador ? (
                      <div
                        style={{
                          minHeight: 40,
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          padding: "0 12px",
                          borderRadius: 8,
                          background: "#F0FDF4",
                          border: "1px solid #BBF7D0",
                        }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#15803D" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M20 6 9 17l-5-5" />
                        </svg>
                        <span style={{ fontWeight: 600, color: "#14532D", fontSize: 13.5 }}>{form.razonSocialComprador}</span>
                        <button
                          type="button"
                          className={clienteErp && !clienteErp.activo ? "bd b-borr" : "bd b-acep"}
                          style={{ marginLeft: "auto", height: 22, border: 0, cursor: "pointer" }}
                          onClick={() => setClienteEstado("")}
                          title="Editar el nombre"
                        >
                          {clienteErp ? (clienteErp.activo ? "Activo" : "Inactivo") : "Guardado"}
                        </button>
                      </div>
                    ) : (
                      <>
                        <input
                          id="rs"
                          className="inp"
                          list="clientes-sugeridos"
                          autoComplete="off"
                          value={form.razonSocialComprador}
                          onChange={(e) => {
                            const elegido = sugerencias.find((c) => c.nombre === e.target.value);
                            if (elegido) aplicarCliente(elegido);
                            else set("razonSocialComprador", e.target.value);
                          }}
                          placeholder="Nombre o razón social (escriba para buscar)"
                        />
                        <datalist id="clientes-sugeridos">
                          {sugerencias
                            .filter((c) => (tipoEcf === "41" || tipoEcf === "47" ? c.tipo !== "cliente" : c.tipo !== "proveedor"))
                            .map((c) => (
                              <option key={c.id} value={c.nombre}>
                                {c.rnc ? formatoRnc(c.rnc) : "Sin RNC"}
                              </option>
                            ))}
                        </datalist>
                      </>
                    )}
                    {clienteEstado === "nuevo" && (
                      <span className="hint">
                        {contactosSinConexion
                          ? "No hay conexión con Contactos: complete los datos a mano."
                          : "Cliente nuevo: se agregará a Contactos al guardar la factura."}
                      </span>
                    )}
                    {clienteEstado === "encontrado" && clienteErp && clienteErp.condicion_pago_dias > 0 && (
                      <span className="hint">Condición del cliente: crédito a {clienteErp.condicion_pago_dias} días.</span>
                    )}
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="cmail">Correo del cliente</label>
                    <input id="cmail" type="email" className="inp" value={form.correoComprador} onChange={(e) => set("correoComprador", e.target.value)} placeholder="opcional" />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="cdir">Dirección del cliente</label>
                    <input id="cdir" className="inp" value={form.direccionComprador} onChange={(e) => set("direccionComprador", e.target.value)} placeholder="opcional" />
                  </div>
                </div>
              )}

              {esNota && (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                    gap: 16,
                    padding: 16,
                    borderRadius: 10,
                    background: "#F8FAFC",
                    border: "1px dashed #CBD5E1",
                  }}
                >
                  <div className="fld">
                    <label className="lbl" htmlFor="afec">
                      e-NCF original afectado <span className="req">*</span>
                    </label>
                    <input
                      id="afec"
                      className="inp mono"
                      list="encf-referenciables"
                      value={form.ncfModificado}
                      onChange={(e) => set("ncfModificado", e.target.value.toUpperCase())}
                      placeholder="E310000000001"
                      maxLength={13}
                    />
                    <datalist id="encf-referenciables">
                      {facturasReferenciables.map((f) => (
                        <option key={f.id} value={f.encf ?? ""}>
                          {new Date(f.creado_en).toLocaleDateString("es-DO")}
                        </option>
                      ))}
                    </datalist>
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="afec-f">
                      Fecha del e-NCF original <span className="req">*</span>
                    </label>
                    <input id="afec-f" type="date" className="inp" value={form.fechaNcfModificado} onChange={(e) => set("fechaNcfModificado", e.target.value)} />
                  </div>
                  <div className="fld">
                    <label className="lbl" htmlFor="cod">
                      Código de modificación <span className="req">*</span>
                    </label>
                    <select id="cod" className="inp" value={form.codigoModificacion} onChange={(e) => set("codigoModificacion", e.target.value)}>
                      {CODIGOS_MODIFICACION.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.value} · {c.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  {config.requiereIndicadorNotaCredito && (
                    <div className="fld">
                      <label className="lbl" htmlFor="ind-nc">¿Más de 30 días desde la factura original?</label>
                      <select id="ind-nc" className="inp" value={form.indicadorNotaCredito} onChange={(e) => set("indicadorNotaCredito", e.target.value)}>
                        <option value="0">No (30 días o menos)</option>
                        <option value="1">Sí (más de 30 días)</option>
                      </select>
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 16 }}>
                <div className="fld">
                  <label className="lbl" htmlFor="femi">Fecha de emisión</label>
                  <input id="femi" type="date" className="inp" value={form.fechaEmision} onChange={(e) => set("fechaEmision", e.target.value)} />
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="fven">
                    Vencimiento del pago {formaPago === "4" && <span className="req">*</span>}
                  </label>
                  <input
                    id="fven"
                    type="date"
                    className="inp"
                    value={form.fechaLimitePago}
                    onChange={(e) => set("fechaLimitePago", e.target.value)}
                    disabled={formaPago !== "4"}
                    title={formaPago !== "4" ? "Solo aplica a ventas a crédito" : undefined}
                  />
                </div>
                {almacenes.length > 0 && (
                  <div className="fld">
                    <label className="lbl" htmlFor="alm">Almacén</label>
                    <select id="alm" className="inp" value={almacenId} onChange={(e) => cambiarAlmacen(e.target.value)}>
                      {almacenes.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.nombre}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <div className="fld">
                  <label className="lbl" htmlFor="encf">
                    e-NCF <span className="req">*</span>
                  </label>
                  <input
                    id="encf"
                    className="inp mono"
                    value={form.encf}
                    onChange={(e) => {
                      setEncfManual(true);
                      set("encf", e.target.value.toUpperCase());
                    }}
                    maxLength={13}
                  />
                </div>
                <div className="fld">
                  <label className="lbl" htmlFor="vend">Vendedor</label>
                  <select id="vend" className="inp" value={vendedorId} onChange={(e) => setVendedorId(e.target.value)}>
                    <option value="">Sin vendedor</option>
                    {vendedores.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.nombre}
                      </option>
                    ))}
                    {vendedorId && !vendedores.some((v) => v.id === vendedorId) && <option value={vendedorId}>Vendedor inactivo</option>}
                  </select>
                </div>
                {config.requiereFechaVencimientoSecuencia && (
                  <div className="fld">
                    <label className="lbl" htmlFor="fsec">
                      Secuencia vence <span className="req">*</span>
                    </label>
                    <input id="fsec" type="date" className="inp" value={form.fechaVencimientoSecuencia} onChange={(e) => set("fechaVencimientoSecuencia", e.target.value)} />
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Productos y servicios</h2>
              <button type="button" className="btn btn-o btn-sm" onClick={agregarLinea}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                Agregar línea
              </button>
            </div>
            <div className="tw">
              <table className="tbl" style={{ minWidth: 900 }}>
                <thead>
                  <tr>
                    <th style={{ width: "30%" }}>Producto</th>
                    <th>Existencia</th>
                    <th style={{ width: 96 }}>Cantidad</th>
                    <th className="num" style={{ width: 120 }}>Precio</th>
                    <th className="num" style={{ width: 80 }}>Desc. %</th>
                    <th style={{ width: 110 }}>
                      <span className="tip" data-tip="18% tasa general · 16% tasa reducida (café, yogur, mantequilla, azúcar, entre otros) · Exento según el Código Tributario.">
                        ITBIS ⓘ
                      </span>
                    </th>
                    <th className="num">Importe (RD$)</th>
                    <th style={{ width: 40 }} aria-label="Quitar" />
                  </tr>
                </thead>
                <tbody>
                  {form.items.map((item, idx) => {
                    const producto = productos.find((p) => p.id === item.productoId);
                    const stock = disponibleDe(item);
                    const cantidad = Number(item.CantidadItem) || 0;
                    const falta = stock != null && stock < cantidad ? cantidad - stock : 0;
                    const lotesLinea = producto?.maneja_lotes ? (lotes[`${producto.id}|${almacenId}`] ?? []) : [];
                    const sugerencia = producto?.maneja_lotes && !item.loteId ? fefo[`${producto.id}|${almacenId}|${cantidad}`] : undefined;
                    return (
                      <tr key={idx}>
                        <td>
                          <select
                            className="inp inp-sm"
                            value={item.productoId ?? ""}
                            onChange={(e) => elegirProducto(idx, e.target.value)}
                            aria-label={`Producto de la línea ${idx + 1}`}
                            style={{ fontWeight: 500 }}
                          >
                            <option value="">Servicio u otro (sin inventario)</option>
                            {productos.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.nombre}
                              </option>
                            ))}
                          </select>
                          {producto ? (
                            <>
                              <div className="hint mono" style={{ marginTop: 4 }}>{producto.sku}</div>
                              {producto.maneja_lotes && (
                                <select
                                  className="inp inp-sm"
                                  style={{ marginTop: 6 }}
                                  value={item.loteId ?? ""}
                                  onChange={(e) => actualizarItem(idx, { loteId: e.target.value || undefined })}
                                  aria-label={`Lote de la línea ${idx + 1}`}
                                >
                                  <option value="">Lote automático (FEFO)</option>
                                  {lotesLinea.map((l) => (
                                    <option key={l.id} value={l.id}>
                                      {l.numero} · {cantidadLegible(l.existencias.find((e) => e.almacen_id === almacenId)?.cantidad ?? 0)} disp.
                                      {l.fecha_vencimiento ? ` · vence ${fechaLegible(l.fecha_vencimiento)}` : ""}
                                      {l.estado === "vencido" ? " · vencido" : l.estado === "bloqueado" ? " · bloqueado" : ""}
                                    </option>
                                  ))}
                                </select>
                              )}
                              {sugerencia && sugerencia.tomas.length > 0 && (
                                <div className="hint" style={{ marginTop: 4 }}>
                                  Sale de {sugerencia.tomas.map((t) => `${t.numero} (${cantidadLegible(t.cantidad)})`).join(" + ")}
                                  {Number(sugerencia.sin_lote) > 0 ? ` + ${cantidadLegible(sugerencia.sin_lote)} sin lote` : ""}
                                </div>
                              )}
                            </>
                          ) : (
                            <input
                              className="inp inp-sm"
                              style={{ marginTop: 6 }}
                              value={item.NombreItem}
                              onChange={(e) => actualizarItem(idx, { NombreItem: e.target.value })}
                              placeholder="Descripción del servicio"
                              aria-label={`Descripción de la línea ${idx + 1}`}
                            />
                          )}
                        </td>
                        <td>
                          {stock == null ? (
                            <span className="mut">—</span>
                          ) : (
                            <>
                              <span style={{ fontVariantNumeric: "tabular-nums", color: falta ? "#B91C1C" : "#111827", fontWeight: 600 }}>
                                {cantidadLegible(stock)} {producto?.unidad_medida || "uds"}
                              </span>
                              {falta > 0 && (
                                <div className="err-t" style={{ fontSize: 12, marginTop: 2 }}>
                                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
                                    <path d="M12 9v4M12 17h.01" />
                                  </svg>
                                  Faltan {cantidadLegible(falta)}
                                </div>
                              )}
                            </>
                          )}
                        </td>
                        <td>
                          <input
                            type="number"
                            min="0"
                            step="any"
                            className="inp inp-sm"
                            style={{ borderColor: falta ? "#DC2626" : undefined }}
                            value={item.CantidadItem}
                            onChange={(e) => cambiarCantidadOPrecio(idx, { CantidadItem: e.target.value })}
                            aria-label={`Cantidad de la línea ${idx + 1}`}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            step="any"
                            className="inp inp-sm"
                            style={{ textAlign: "right" }}
                            value={item.PrecioUnitarioItem}
                            onChange={(e) => cambiarCantidadOPrecio(idx, { PrecioUnitarioItem: e.target.value })}
                            aria-label={`Precio de la línea ${idx + 1}`}
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="any"
                            className="inp inp-sm"
                            style={{ textAlign: "right" }}
                            value={porcentajeDescuento(item)}
                            onChange={(e) => cambiarDescuento(idx, e.target.value)}
                            placeholder="—"
                            aria-label={`Descuento de la línea ${idx + 1}`}
                          />
                        </td>
                        <td>
                          <select
                            className="inp inp-sm"
                            value={item.IndicadorFacturacion}
                            onChange={(e) => actualizarItem(idx, { IndicadorFacturacion: e.target.value })}
                            aria-label={`ITBIS de la línea ${idx + 1}`}
                          >
                            {TASAS.map((t) => (
                              <option key={t.value} value={t.value}>
                                {t.label}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="num" style={{ fontWeight: 600, color: "#111827" }}>
                          {formatoMonto(calcularMontoItem(item))}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="ib"
                            style={{ width: 30, height: 30 }}
                            onClick={() => quitarLinea(idx)}
                            disabled={form.items.length <= 1}
                            aria-label={`Quitar línea ${idx + 1}`}
                          >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                              <path d="M18 6 6 18M6 6l12 12" />
                            </svg>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {faltaStock && (
              <div style={{ padding: "0 20px 16px" }}>
                <div className="al al-w">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
                    <path d="M3 8l9 5 9-5" />
                  </svg>
                  <span>
                    <b>Existencia insuficiente.</b> Hay líneas con cantidad mayor al disponible
                    {almacen ? ` en el almacén ${almacen.nombre}` : ""}. Puede guardar el borrador, pero para emitir debe ajustar la cantidad,
                    elegir otro almacén o registrar la compra de la mercancía.
                  </span>
                </div>
              </div>
            )}
            {productos.length === 0 && (
              <div style={{ padding: "0 20px 16px" }}>
                <span className="hint">
                  No hay productos en el catálogo. Puede facturar servicios o <Link to="/inventario/productos/nuevo">crear un producto</Link>.
                </span>
              </div>
            )}
          </section>
        </div>

        <div style={{ flex: "1 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <section className="card">
            <div className="card-h">
              <h2>Totales</h2>
              <span className="chip mono">{form.encf || proximoEncf}</span>
            </div>
            <dl className="kv card-b" style={{ gridTemplateColumns: "1fr auto" }}>
              <dt>Subtotal</dt>
              <dd className="num">{formatoMonto(totales.subtotal)}</dd>
              <dt>Descuentos</dt>
              <dd className="num">-{formatoMonto(totales.descuentos)}</dd>
              <dt>Gravado 18%</dt>
              <dd className="num">{formatoMonto(totales.g18)}</dd>
              <dt>Gravado 16%</dt>
              <dd className="num">{formatoMonto(totales.g16)}</dd>
              {totales.g0 > 0 && (
                <>
                  <dt>Gravado 0%</dt>
                  <dd className="num">{formatoMonto(totales.g0)}</dd>
                </>
              )}
              <dt>Exento</dt>
              <dd className="num">{formatoMonto(totales.exento)}</dd>
              <dt>ITBIS 18%</dt>
              <dd className="num">{formatoMonto(totales.i18)}</dd>
              <dt>ITBIS 16%</dt>
              <dd className="num">{formatoMonto(totales.i16)}</dd>
              <dt style={{ fontSize: 15, fontWeight: 650, color: "#111827", borderTop: "1px solid #E5E7EB", paddingTop: 12 }}>Total a pagar</dt>
              <dd className="num" style={{ fontSize: 20, fontWeight: 700, borderTop: "1px solid #E5E7EB", paddingTop: 10 }}>
                RD$ {formatoMonto(totales.total)}
              </dd>
            </dl>
          </section>

          <section className="card">
            <div className="card-h">
              <h2>Forma de pago</h2>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="seg" role="group" aria-label="Forma de pago" style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
                {PAGOS.map((p) => (
                  <button
                    key={p.value}
                    type="button"
                    className={formaPago === p.value ? "on" : ""}
                    onClick={() => {
                      setFormaPago(p.value);
                      if (p.value === "4" && !form.fechaLimitePago) set("fechaLimitePago", sumarDias(form.fechaEmision, 30));
                    }}
                    aria-pressed={formaPago === p.value}
                    style={{ justifyContent: "center" }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <span className="hint">
                {formaPago === "4"
                  ? `Condición: vence el ${fechaLegible(form.fechaLimitePago)}. Quedará como cuenta por cobrar al cliente.`
                  : "Pago de contado: la factura queda saldada al emitirse."}
              </span>
              {config.requiereTipoIngresos && (
                <div className="fld">
                  <label className="lbl" htmlFor="ting">Tipo de ingresos</label>
                  <select id="ting" className="inp" value={form.tipoIngresos} onChange={(e) => set("tipoIngresos", e.target.value)}>
                    {TIPOS_INGRESOS.map((t) => (
                      <option key={t.value} value={t.value}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          </section>

          {error && (
            <div className="al al-d" role="alert">
              <IconoAlerta />
              <span>
                <b>{error}</b>
                {erroresXml.length > 0 && (
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {erroresXml.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                )}
              </span>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <button type="button" className="btn btn-p" onClick={pedirConfirmacion} disabled={procesando !== "" || Boolean(facturaCompleta && facturaCompleta.estado !== "borrador")} style={{ height: 46 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m22 2-7 20-4-9-9-4z" />
                <path d="M22 2 11 13" />
              </svg>
              {procesando === "emitir" ? "Emitiendo…" : "Emitir y enviar a DGII"}
            </button>
            <button type="button" className="btn btn-o" onClick={guardarBorrador} disabled={procesando !== "" || Boolean(facturaCompleta && facturaCompleta.estado !== "borrador")}>
              {procesando === "borrador" ? "Guardando…" : editando ? "Guardar cambios" : "Guardar borrador"}
            </button>
            <span className="hint" style={{ textAlign: "center" }}>
              El borrador no se envía a la DGII.
            </span>
          </div>
        </div>
      </div>

      {editando && facturaCompleta && (
        <section className="card">
          <div className="card-h">
            <h2>Acciones avanzadas</h2>
          </div>
          <div className="card-b">
            <FacturaAcciones factura={facturaCompleta} onRecargar={() => id && api.obtener(id).then(setFacturaCompleta)} />
          </div>
        </section>
      )}

      {confirmar && (
        <div className="scrim">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="emitir-t">
            <div className="modal-h">
              <h3 id="emitir-t">¿Emitir y enviar a la DGII?</h3>
            </div>
            <div className="modal-b">
              <span>
                Se firmará digitalmente el comprobante{" "}
                <b className="mono" style={{ color: "#111827" }}>
                  {form.encf}
                </b>{" "}
                ({tipoActual?.label}) por <b style={{ color: "#111827" }}>RD$ {formatoMonto(totales.total)}</b> y se enviará a la DGII.
              </span>
              <div className="al al-w">
                <IconoAlerta />
                <span>
                  Una vez enviado, el e-NCF queda utilizado y el comprobante no puede editarse. Para corregirlo deberá emitir una nota de
                  crédito o débito.
                </span>
              </div>
            </div>
            <div className="modal-f">
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(false)}>
                Revisar
              </button>
              <button type="button" className="btn btn-p" onClick={emitir}>
                Sí, emitir
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TarjetaTipo({
  tipo,
  activo,
  deshabilitado,
  onElegir,
}: {
  tipo: { code: string; label: string; desc: string };
  activo: boolean;
  deshabilitado: boolean;
  onElegir: (code: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onElegir(tipo.code)}
      aria-pressed={activo}
      disabled={deshabilitado && !activo}
      style={{
        textAlign: "left",
        padding: "12px 14px",
        borderRadius: 10,
        cursor: deshabilitado ? "default" : "pointer",
        font: "inherit",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        border: activo ? "1.5px solid #0F766E" : "1px solid #E5E7EB",
        background: activo ? "#F0FAF9" : "#fff",
        boxShadow: activo ? "0 0 0 3px rgba(15,118,110,.12)" : "none",
        opacity: deshabilitado && !activo ? 0.55 : 1,
      }}
    >
      <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: activo ? "#0F766E" : "#6B7280" }}>
        E{tipo.code}
      </span>
      <span style={{ fontSize: 13.5, fontWeight: 600, color: "#111827" }}>{tipo.label}</span>
      <span style={{ fontSize: 12, color: "#6B7280", lineHeight: 1.35 }}>{tipo.desc}</span>
    </button>
  );
}
