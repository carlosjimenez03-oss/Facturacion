// POS (Kontara POS, modal "Conecta tu punto de venta" de Sidebar.dc.html):
// caja con apertura (fondo) y cierre (arqueo por método), cuadrícula de
// productos con existencia del almacén de la caja, carrito y cobro de contado
// (efectivo con cambio, tarjeta, transferencia o mixto). Cada venta emite un
// E32 (consumo) -- o un E31 si se indica RNC/cédula -- por el mismo flujo de
// facturas-api que Nueva factura (crear -> generar-xml -> enviar), con los
// helpers de buildDatos.ts, y queda registrada en la sesión de caja de
// erp-api. Si la DGII no responde, facturas-api la emite en contingencia.
//
// Sin conexión con el servidor (internet caído en la tienda) el POS sigue
// vendiendo con la caja abierta y el catálogo guardado en el navegador: la
// venta se guarda en IndexedDB con su clave de idempotencia, se entrega un
// ticket provisional (sin e-NCF: lo asigna el servidor) y al reconectar se
// sincroniza en orden por el mismo flujo (ver posOffline.ts,
// usePosSinConexion.ts y docs/dgii/CONTINGENCIA.md, "POS sin conexión").
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { IconPos } from "../../layout/icons";
import { api } from "../../lib/api";
import { configuracionApi, publicarEmpresa } from "../../lib/configuracionApi";
import type { Secuencia } from "../../lib/configuracionApi";
import { construirDatos, construirItemsInventario, desglose, siguienteEncf } from "../../lib/buildDatos";
import { erpApi } from "../../lib/erpApi";
import type { Cliente } from "../../lib/erpApi";
import { inventarioApi } from "../../lib/inventarioApi";
import { formatoMonto, MARCA } from "../../lib/marca";
import { guardarEmisor, guardarVencimientoSecuencia, leerEmisor, leerVencimientoSecuencia } from "../../lib/preferencias";
import type { DatosEmisor } from "../../lib/preferencias";
import { CAJA_PRINCIPAL, solucionesApi } from "../../lib/solucionesApi";
import type { MetodoPos, MontosPorMetodo, PagoPos, SesionCaja, Timbre } from "../../lib/solucionesApi";
import { configParaTipo } from "../../lib/tipoConfig";
import type { Almacen, Producto } from "../../lib/types";
import { AvisoError, IconoBuscar, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { actualizarCatalogo, leerCatalogo } from "./posAlmacen";
import type { CatalogoPos } from "./posAlmacen";
import {
  esErrorDeRed,
  nuevaClave,
  nuevaVentaOffline,
  referenciaVenta,
  resumenPendiente,
  sinSincronizar,
  stockConPendientes,
} from "./posOffline";
import type { ResultadoSincronizacion, VentaOffline } from "./posOffline";
import { formularioVenta, itemsCarrito, tipoParaCliente, TOPE_CONSUMO_SIN_IDENTIFICAR, validarPagos } from "./posUtil";
import type { LineaCarrito } from "./posUtil";
import { Ticket } from "./Ticket";
import type { DatosTicket } from "./Ticket";
import { usePosSinConexion } from "./usePosSinConexion";
import "./pos.css";

type ModoPago = MetodoPos | "mixto";

const METODOS: { value: MetodoPos; label: string }[] = [
  { value: "efectivo", label: "Efectivo" },
  { value: "tarjeta", label: "Tarjeta" },
  { value: "transferencia", label: "Transferencia" },
];

const ESTADO_DGII: Record<string, string> = {
  aceptado: "Aceptado",
  enviado: "Recibido, en validación",
  rechazado: "Rechazado",
  error: "Error de envío",
  contingencia: "Emitido en contingencia, pendiente de envío",
};

const n2 = (n: number) => Math.round((n + Number.EPSILON) * 100) / 100;

const SIN_CONEXION = "Sin conexión con el servidor.";

/** Ticket de una venta hecha sin conexión: provisional hasta sincronizarse, luego con su e-NCF. */
function ticketDeVentaOffline(v: VentaOffline): DatosTicket {
  const sincronizada = v.estado === "sincronizada" && Boolean(v.encf);
  return {
    facturaId: v.facturaId ?? "",
    tipoEcf: v.tipoEcf,
    encf: sincronizada ? (v.encf as string) : "",
    fecha: new Date(v.creadaEn),
    emisor: v.emisor,
    cliente: v.cliente,
    items: v.items,
    pagos: v.pagos,
    recibido: v.recibido,
    cambio: v.cambio,
    estadoDgii: ESTADO_DGII[v.estadoDgii] ?? v.estadoDgii,
    caja: v.caja,
    cajero: v.cajero,
    contingencia: v.contingencia,
    provisional: sincronizada ? undefined : { referencia: referenciaVenta(v.clave) },
  };
}

/** Venta a medio emitir (falló un paso): se reintenta sin crear otro e-CF. */
interface VentaPendiente {
  facturaId: string;
  /** El XML se generó y validó contra el XSD: el reintento no lo envía sin esto. */
  xmlValido: boolean;
  enviada: boolean;
  ticket: DatosTicket;
}

/** Secuencia que no deja emitir (igual que `secuenciaBloquea` de Nueva factura). */
function secuenciaBloqueada(s: Secuencia | undefined): string | null {
  if (!s?.configurada) return null;
  if (s.estado === "inactiva") return `La secuencia ${s.prefijo} está desactivada: actívela en Configuración → Secuencias autorizadas.`;
  if (s.estado === "vencida") return `La secuencia ${s.prefijo} está vencida: registre la nueva en Configuración → Secuencias autorizadas.`;
  if (s.estado === "agotada") return `La secuencia ${s.prefijo} está agotada: registre una nueva en Configuración → Secuencias autorizadas.`;
  return null;
}

function Promo({ onProbar, onSeguir }: { onProbar: () => void; onSeguir: () => void }) {
  return (
    <section className="card pos-promo">
      <div className="pos-promo-txt">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="pos-logo">
            <IconPos />
          </span>
          <span style={{ lineHeight: 1 }}>
            <span style={{ display: "block", fontSize: 10, color: "#6B7280" }}>{MARCA.toLowerCase()}</span>
            <b style={{ fontSize: 17, color: "#111827" }}>POS</b>
          </span>
        </div>
        <h2>
          Conecta tu <span style={{ color: "#14B8A6" }}>punto de venta</span> y automatiza ventas
        </h2>
        <p>Al conectar {MARCA} POS con tu facturación, podrás:</p>
        <ul>
          <li>Emitir e-CF de consumo desde caja y actualizar tu inventario al instante.</li>
          <li>Cerrar la caja con arqueo por forma de pago, sin cuadres a mano.</li>
          <li>Ver caja, ventas e ingresos en un solo lugar.</li>
        </ul>
        <div className="row" style={{ gap: 16, marginTop: 8 }}>
          <button type="button" className="btn pos-probar" onClick={onProbar}>
            Probar {MARCA} POS
          </button>
          <button type="button" className="btn btn-g" style={{ color: "#0F766E" }} onClick={onSeguir}>
            Seguir en Facturación
          </button>
        </div>
      </div>
      <div aria-hidden="true" className="pos-promo-ilus">
        <div>
          <div className="pos-promo-barra">
            <span />
            <span />
          </div>
          <div className="pos-promo-grid">
            {Array.from({ length: 6 }, (_, i) => (
              <span key={i}>
                <i />
                <b />
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export function PosPage() {
  const navigate = useNavigate();
  const [sesion, setSesion] = useState<SesionCaja | null>(null);
  const [historial, setHistorial] = useState<SesionCaja[]>([]);
  const [cargando, setCargando] = useState(true);
  const [errorCarga, setErrorCarga] = useState<string | null>(null);
  const [verPromo, setVerPromo] = useState(false);
  const [almacenes, setAlmacenes] = useState<Almacen[]>([]);
  const [apertura, setApertura] = useState({ fondo: "", cajero: "", almacenId: "" });
  const [abriendo, setAbriendo] = useState(false);

  const [productos, setProductos] = useState<Producto[]>([]);
  const [stock, setStock] = useState<Record<string, number>>({});
  const [q, setQ] = useState("");
  const [categoria, setCategoria] = useState("");
  const [carrito, setCarrito] = useState<LineaCarrito[]>([]);
  const [cliente, setCliente] = useState({ rnc: "", nombre: "" });
  const [verificando, setVerificando] = useState(false);

  const [cobrando, setCobrando] = useState(false);
  const [modo, setModo] = useState<ModoPago>("efectivo");
  const [montos, setMontos] = useState<Record<MetodoPos, string>>({ efectivo: "", tarjeta: "", transferencia: "" });
  const [referencia, setReferencia] = useState("");
  const [procesando, setProcesando] = useState("");
  const [errorVenta, setErrorVenta] = useState<string | null>(null);
  const [erroresXml, setErroresXml] = useState<string[]>([]);
  const [pendiente, setPendiente] = useState<VentaPendiente | null>(null);
  const [ticket, setTicket] = useState<{ datos: DatosTicket; timbre: Timbre | null } | null>(null);

  const [config, setConfig] = useState(false);
  const [emisor, setEmisor] = useState<DatosEmisor>(leerEmisor);
  const [vence, setVence] = useState({ "31": leerVencimientoSecuencia("31"), "32": "" });
  const [secuencias, setSecuencias] = useState<Secuencia[]>([]);
  const [cerrar, setCerrar] = useState(false);
  const [contado, setContado] = useState<MontosPorMetodo>({ efectivo: "", tarjeta: "", transferencia: "" });
  const [notasCierre, setNotasCierre] = useState("");
  const [cerrando, setCerrando] = useState(false);
  const [cierre, setCierre] = useState<SesionCaja | null>(null);
  const [toast, mostrarToast] = useAviso();
  const buscador = useRef<HTMLInputElement>(null);
  /** Clave de idempotencia de la venta que se está cobrando (una por cobro, también en línea). */
  const claveVenta = useRef<string>("");
  const [clientesConRnc, setClientesConRnc] = useState<{ rnc: string; nombre: string }[]>([]);
  const recargarTodo = useRef<() => void>(() => undefined);
  const alSincronizar = useRef<(r: ResultadoSincronizacion) => void>(() => undefined);
  const conexion = usePosSinConexion({
    alReconectar: () => recargarTodo.current(),
    alSincronizar: (r) => alSincronizar.current(r),
  });
  const { marcarSinConexion } = conexion;

  /** Sin red: lo último que el POS guardó en este navegador. */
  const catalogoGuardado = useCallback(async (): Promise<CatalogoPos | null> => {
    try {
      return await leerCatalogo();
    } catch {
      return null;
    }
  }, []);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const [abierta, hist] = await Promise.all([solucionesApi.sesionAbierta(CAJA_PRINCIPAL), solucionesApi.listarSesiones(10)]);
      setSesion(abierta);
      setHistorial(hist);
      setVerPromo(!abierta && hist.length === 0);
      setErrorCarga(null);
      void actualizarCatalogo({ sesion: abierta }).catch(() => undefined);
    } catch (err) {
      const sinRed = esErrorDeRed(err);
      const guardado = sinRed ? await catalogoGuardado() : null;
      if (sinRed) marcarSinConexion();
      if (guardado?.sesion) {
        // Sin conexión con la caja que estaba abierta: se sigue vendiendo.
        const abierta = guardado.sesion;
        setSesion((s) => s ?? abierta);
        setVerPromo(false);
        setErrorCarga(null);
      } else {
        setErrorCarga(
          sinRed
            ? `${SIN_CONEXION} Para vender sin conexión, la caja debe haberse abierto en este equipo mientras había conexión.`
            : mensajeError(err),
        );
      }
    } finally {
      setCargando(false);
    }
    inventarioApi.listarAlmacenes({ activo: true }).then(
      (l) => {
        setAlmacenes(l);
        void actualizarCatalogo({ almacenes: l }).catch(() => undefined);
      },
      () => void catalogoGuardado().then((c) => c && setAlmacenes((a) => (a.length ? a : c.almacenes))),
    );
  }, [catalogoGuardado, marcarSinConexion]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // La caja abierta (y sus totales) para poder seguir vendiendo sin conexión.
  useEffect(() => {
    if (sesion) void actualizarCatalogo({ sesion }).catch(() => undefined);
  }, [sesion]);

  // Emisor y vencimiento de la secuencia E31 desde Configuración (igual que
  // Nueva factura); si erp-api no responde, lo recordado en este navegador.
  useEffect(() => {
    configuracionApi
      .empresa()
      .then((e) => {
        publicarEmpresa(e);
        if (e.configurada)
          setEmisor({ rnc: e.rnc ?? "", razonSocial: e.razon_social, direccion: e.direccion ?? "", correo: e.correo ?? "" });
      })
      .catch(() => undefined);
    configuracionApi
      .secuencias()
      .then((l) => {
        setSecuencias(l);
        void actualizarCatalogo({ secuencias: l }).catch(() => undefined);
        const e31 = l.find((x) => x.tipo_ecf === 31 && x.configurada);
        if (e31?.fecha_vencimiento) setVence((v) => ({ ...v, "31": e31.fecha_vencimiento ?? v["31"] }));
      })
      .catch(() => void catalogoGuardado().then((c) => c && setSecuencias((s) => (s.length ? s : c.secuencias))));
    // Clientes con RNC/cédula (solo RNC y nombre) para «Verificar» sin conexión.
    erpApi
      .listarClientes({ activo: true })
      .then((l: Cliente[]) => {
        const clientes = l.filter((c) => c.rnc).map((c) => ({ rnc: c.rnc as string, nombre: c.nombre }));
        setClientesConRnc(clientes);
        void actualizarCatalogo({ clientes }).catch(() => undefined);
      })
      .catch(() => void catalogoGuardado().then((c) => c && setClientesConRnc(c.clientes)));
  }, [catalogoGuardado]);

  const almacenCaja = sesion?.almacen_id || almacenes.find((a) => a.es_principal)?.id || "";

  const cargarProductos = useCallback(async () => {
    try {
      // inventario-api pagina de a 200 como máximo.
      const todos: Producto[] = [];
      for (let offset = 0; ; offset += 200) {
        const pagina = await inventarioApi.listarProductos({ activo: true, limit: 200, offset });
        todos.push(...pagina);
        if (pagina.length < 200) break;
      }
      setProductos(todos);
      void actualizarCatalogo({ productos: todos }).catch(() => undefined);
    } catch (err) {
      const guardado = esErrorDeRed(err) ? await catalogoGuardado() : null;
      if (guardado && guardado.productos.length > 0) {
        marcarSinConexion();
        setProductos((p) => (p.length ? p : guardado.productos));
      } else setErrorCarga(mensajeError(err));
    }
  }, [catalogoGuardado, marcarSinConexion]);

  const cargarStock = useCallback(
    async (almacenId: string) => {
      if (!almacenId) return;
      try {
        const ex = await inventarioApi.existenciasAlmacen(almacenId, { incluir_cero: true });
        const cantidades = Object.fromEntries(ex.map((e) => [e.producto_id, Number(e.cantidad)]));
        setStock(cantidades);
        void actualizarCatalogo({ almacenId, stock: cantidades }).catch(() => undefined);
      } catch (err) {
        // Sin red: la existencia guardada (si es de este almacén); si no, se conserva la que había.
        const sinRed = esErrorDeRed(err);
        const guardado = sinRed ? await catalogoGuardado() : null;
        if (guardado?.almacenId === almacenId) setStock(guardado.stock);
        else if (!sinRed) setStock({});
      }
    },
    [catalogoGuardado],
  );

  useEffect(() => {
    if (!sesion) return;
    void cargarProductos();
  }, [sesion?.id, cargarProductos]);

  useEffect(() => {
    if (sesion) void cargarStock(almacenCaja);
  }, [sesion?.id, almacenCaja, cargarStock]);

  // Al reconectar: caja, catálogo, existencia y secuencias frescos.
  recargarTodo.current = () => {
    void cargar();
    void cargarProductos();
    void cargarStock(almacenCaja);
    configuracionApi.secuencias().then(setSecuencias, () => undefined);
  };

  // Tras sincronizar: totales de la caja y existencia del servidor.
  alSincronizar.current = (r) => {
    if (r.sincronizadas.length > 0) {
      mostrarToast(`${r.sincronizadas.length} venta(s) sin conexión sincronizada(s)`, {
        detalle: r.sincronizadas.map((v) => v.encf).join(", "),
      });
      if (sesion) void solucionesApi.obtenerSesion(sesion.id).then(setSesion, () => undefined);
      void cargarStock(almacenCaja);
    }
    if (r.conError.length > 0)
      mostrarToast(`${r.conError.length} venta(s) sin conexión no se pudieron emitir`, { detalle: r.conError[0].error ?? "", error: true });
  };

  const pendientesCaja = useMemo(() => (sesion ? resumenPendiente(conexion.cola, sesion.id) : null), [conexion.cola, sesion]);
  const porSincronizarCaja = sesion ? sinSincronizar(conexion.cola, sesion.id).length : 0;
  const porSincronizar = sinSincronizar(conexion.cola).length;
  const stockVisible = useMemo(() => stockConPendientes(stock, conexion.cola, almacenCaja), [stock, conexion.cola, almacenCaja]);

  const categorias = useMemo(() => [...new Set(productos.map((p) => p.categoria).filter(Boolean) as string[])].sort(), [productos]);
  const visibles = useMemo(() => {
    const t = q.trim().toLowerCase();
    return productos.filter(
      (p) => (!categoria || p.categoria === categoria) && (!t || [p.nombre, p.sku, p.codigo_barras].some((x) => x?.toLowerCase().includes(t))),
    );
  }, [productos, q, categoria]);

  const disponible = (p: Producto) => (p.tipo === "servicio" ? Infinity : (stockVisible[p.id] ?? 0));
  const enCarrito = (id: string) => carrito.find((l) => l.producto.id === id)?.cantidad ?? 0;

  function agregar(p: Producto, cantidad = 1) {
    if (enCarrito(p.id) + cantidad > disponible(p)) {
      mostrarToast("Sin existencia suficiente", { detalle: `${p.nombre}: ${disponible(p)} disponible(s) en el almacén de la caja`, error: true });
      return;
    }
    setCarrito((prev) => {
      const i = prev.findIndex((l) => l.producto.id === p.id);
      if (i < 0) return [...prev, { producto: p, cantidad }];
      const copia = prev.slice();
      copia[i] = { ...copia[i], cantidad: copia[i].cantidad + cantidad };
      return copia;
    });
  }

  function cambiarCantidad(id: string, cantidad: number) {
    setCarrito((prev) =>
      prev.flatMap((l) => {
        if (l.producto.id !== id) return [l];
        if (cantidad <= 0) return [];
        return [{ ...l, cantidad: Math.min(cantidad, disponible(l.producto)) }];
      }),
    );
  }

  function buscarEnter() {
    const t = q.trim().toLowerCase();
    const exacto = productos.find((p) => p.codigo_barras?.toLowerCase() === t || p.sku.toLowerCase() === t);
    const unico = exacto ?? (visibles.length === 1 ? visibles[0] : null);
    if (unico) {
      agregar(unico);
      setQ("");
    }
  }

  async function verificarCliente() {
    const rnc = cliente.rnc.replace(/\D/g, "");
    if (!rnc) return;
    // Sin conexión: los clientes guardados en este equipo.
    const guardado = clientesConRnc.find((c) => c.rnc === rnc);
    const sinGuardar = () => mostrarToast("Sin conexión: RNC/cédula no guardado en este equipo", { detalle: "Escriba la razón social del cliente." });
    if (!conexion.enLinea) {
      if (guardado) setCliente({ rnc, nombre: guardado.nombre });
      else sinGuardar();
      return;
    }
    setVerificando(true);
    try {
      const c = await erpApi.clientePorRnc(rnc);
      setCliente({ rnc, nombre: c.nombre });
    } catch (err) {
      if (guardado) setCliente({ rnc, nombre: guardado.nombre });
      else if (esErrorDeRed(err)) sinGuardar();
      else mostrarToast("RNC/cédula sin registrar en Contactos", { detalle: "Escriba la razón social del cliente." });
    } finally {
      setVerificando(false);
    }
  }

  const items = useMemo(() => itemsCarrito(carrito), [carrito]);
  const totales = useMemo(() => desglose(items), [items]);
  const total = n2(totales.total);
  const tipoEcf = tipoParaCliente(cliente.rnc);

  const pagos: PagoPos[] = useMemo(() => {
    if (modo === "mixto") {
      return METODOS.filter((m) => Number(montos[m.value]) > 0).map((m) => ({ metodo: m.value, monto: n2(Number(montos[m.value])).toFixed(2) }));
    }
    const monto = modo === "efectivo" ? Number(montos.efectivo) || 0 : total;
    return [{ metodo: modo, monto: n2(monto).toFixed(2), referencia: modo !== "efectivo" && referencia ? referencia : null }];
  }, [modo, montos, total, referencia]);
  const resultadoPagos = validarPagos(total, pagos);

  function problemaVenta(): string | null {
    if (carrito.length === 0) return "Agregue al menos un producto.";
    const bloqueo = secuenciaBloqueada(secuencias.find((s) => String(s.tipo_ecf) === tipoEcf));
    if (bloqueo) return bloqueo;
    if (!emisor.rnc || !emisor.razonSocial || !emisor.direccion) return "Complete los datos del emisor en «Configurar caja».";
    if (configParaTipo(tipoEcf).requiereFechaVencimientoSecuencia && !vence[tipoEcf])
      return `Indique hasta cuándo está autorizada la secuencia E${tipoEcf} en «Configurar caja».`;
    const rnc = cliente.rnc.replace(/\D/g, "");
    if (rnc && rnc.length !== 9 && rnc.length !== 11) return "El RNC debe tener 9 dígitos o la cédula 11.";
    if (rnc && !cliente.nombre.trim()) return "Escriba la razón social del cliente (o pulse Verificar).";
    if (!rnc && total > TOPE_CONSUMO_SIN_IDENTIFICAR) return "El total supera RD$ 250,000.00: identifique al comprador con RNC o cédula.";
    return null;
  }

  function abrirCobro() {
    const p = problemaVenta();
    if (p) {
      if (p.includes("Configurar caja")) setConfig(true);
      mostrarToast("No se puede cobrar todavía", { detalle: p, error: true });
      return;
    }
    claveVenta.current = nuevaClave();
    setModo("efectivo");
    setMontos({ efectivo: total.toFixed(2), tarjeta: "", transferencia: "" });
    setReferencia("");
    setErrorVenta(null);
    setErroresXml([]);
    setCobrando(true);
  }

  async function terminarVenta(datos: DatosTicket) {
    setProcesando("Preparando el ticket…");
    let timbre: Timbre | null = null;
    try {
      // La vista previa firmada da el código de seguridad y el QR (igual que el PDF de la factura).
      await api.firmar(datos.facturaId);
      timbre = await solucionesApi.timbre(datos.facturaId);
    } catch {
      timbre = null;
    }
    setTicket({ datos, timbre });
    setPendiente(null);
    setCarrito([]);
    setCliente({ rnc: "", nombre: "" });
    setCobrando(false);
    if (sesion) setSesion(await solucionesApi.obtenerSesion(sesion.id).catch(() => sesion));
    void cargarStock(almacenCaja);
  }

  /**
   * Guarda la venta en la cola del navegador (sin conexión con el servidor)
   * con la clave de idempotencia del cobro y entrega el ticket provisional.
   * También cuando la red se cae a mitad de una venta en línea: si la
   * factura ya se había creado, la sincronización la recupera por la misma
   * clave en vez de crear otra.
   */
  async function venderSinConexion() {
    if (!sesion) return;
    if (!conexion.almacenamiento) {
      setErrorVenta(`${SIN_CONEXION} Este navegador no permite guardar ventas sin conexión: reintente cuando vuelva la conexión.`);
      return;
    }
    setProcesando("Guardando la venta sin conexión…");
    try {
      const form = formularioVenta({
        tipoEcf,
        encf: "",
        vencimientoSecuencia: vence[tipoEcf],
        emisor,
        cliente,
        lineas: carrito,
        pagos,
        cambio: resultadoPagos.cambio,
        almacenId: almacenCaja,
      });
      const venta = nuevaVentaOffline({
        clave: claveVenta.current || nuevaClave(),
        creadaEn: new Date().toISOString(),
        sesionId: sesion.id,
        caja: sesion.caja,
        cajero: sesion.cajero,
        tipoEcf,
        datos: construirDatos(form, configParaTipo(tipoEcf)),
        itemsInventario: construirItemsInventario(form.items, almacenCaja),
        items: form.items,
        emisor,
        cliente: { rnc: form.compradorRnc, nombre: form.razonSocialComprador },
        pagos,
        recibido: resultadoPagos.recibido,
        cambio: resultadoPagos.cambio,
        total,
      });
      await conexion.encolar(venta);
      guardarEmisor(emisor);
      setTicket({ datos: ticketDeVentaOffline(venta), timbre: null });
      setPendiente(null);
      setCarrito([]);
      setCliente({ rnc: "", nombre: "" });
      setCobrando(false);
    } catch (err) {
      setErrorVenta(`No se pudo guardar la venta sin conexión: ${mensajeError(err)}`);
    } finally {
      setProcesando("");
    }
  }

  async function emitir() {
    if (!sesion || resultadoPagos.error) return;
    setErrorVenta(null);
    setErroresXml([]);
    if (!conexion.enLinea && conexion.almacenamiento) {
      await venderSinConexion();
      return;
    }
    let actual = pendiente;
    try {
      if (!actual) {
        setProcesando("Generando el e-CF…");
        // Con la secuencia registrada en Configuración el e-NCF lo asigna el
        // servidor al crear (sin duplicados entre cajas); se vuelve a mirar
        // su estado por si cambió desde que se abrió la caja.
        const lista = await configuracionApi.secuencias().catch(() => secuencias);
        setSecuencias(lista);
        const secuencia = lista.find((x) => String(x.tipo_ecf) === tipoEcf && x.configurada);
        const bloqueo = secuenciaBloqueada(secuencia);
        if (bloqueo) throw new Error(bloqueo);
        // Sin secuencia registrada: el mayor ya usado de este tipo + 1 (el servidor rechaza un duplicado).
        const encfLocal = secuencia ? "" : siguienteEncf(await api.listar({ tipo_ecf: Number(tipoEcf) }), tipoEcf);
        const form = formularioVenta({
          tipoEcf,
          encf: encfLocal,
          vencimientoSecuencia: vence[tipoEcf],
          emisor,
          cliente,
          lineas: carrito,
          pagos,
          cambio: resultadoPagos.cambio,
          almacenId: almacenCaja,
        });
        const datos = construirDatos(form, configParaTipo(tipoEcf));
        const creada = await api.crear({
          tipo_ecf: Number(tipoEcf),
          encf: secuencia ? null : encfLocal,
          asignar_encf: Boolean(secuencia),
          datos,
          items_inventario: construirItemsInventario(form.items, almacenCaja),
          clave_idempotencia: claveVenta.current || null,
        });
        const encf = creada.encf ?? encfLocal;
        actual = {
          facturaId: creada.id,
          xmlValido: false,
          enviada: false,
          ticket: {
            facturaId: creada.id,
            tipoEcf,
            encf,
            fecha: new Date(),
            emisor,
            cliente: { rnc: form.compradorRnc, nombre: form.razonSocialComprador },
            items: form.items,
            pagos,
            recibido: resultadoPagos.recibido,
            cambio: resultadoPagos.cambio,
            estadoDgii: "",
            caja: sesion.caja,
            cajero: sesion.cajero,
          },
        };
        setPendiente(actual);
      }
      // También en el reintento: nunca se envía un e-CF cuyo XML no pasó el XSD
      // (p. ej. si la red falló justo al generarlo).
      if (!actual.enviada && !actual.xmlValido) {
        setProcesando("Validando el e-CF…");
        const xml = await api.generarXml(actual.facturaId);
        if (xml.errores.length > 0) {
          setErroresXml(xml.errores);
          throw new Error("La DGII no aceptaría este comprobante. Corrija lo indicado o cancele la venta.");
        }
        actual = { ...actual, xmlValido: true };
        setPendiente(actual);
      }
      if (!actual.enviada) {
        setProcesando("Enviando a la DGII…");
        const envio = await api.enviar(actual.facturaId);
        // Sin conexión con la DGII la venta sigue: el e-CF quedó firmado en
        // contingencia, el ticket lleva la leyenda y se envía solo al reconectar.
        if (!envio.track_id && !envio.contingencia) throw new Error(envio.error || "La DGII no recibió el comprobante. Puede reintentar el envío.");
        actual = {
          ...actual,
          enviada: true,
          ticket: { ...actual.ticket, estadoDgii: ESTADO_DGII[envio.estado] ?? envio.estado, contingencia: Boolean(envio.contingencia) },
        };
        setPendiente(actual);
      }
      setProcesando("Registrando en caja…");
      await solucionesApi.registrarVenta(sesion.id, { factura_id: actual.facturaId, pagos: actual.ticket.pagos });
      guardarEmisor(emisor);
      await terminarVenta(actual.ticket);
    } catch (err) {
      if (esErrorDeRed(err) && conexion.almacenamiento) {
        // Se cayó la conexión con el servidor: la venta sigue sin conexión.
        marcarSinConexion();
        setProcesando("");
        await venderSinConexion();
        return;
      }
      setErrorVenta(mensajeError(err));
    } finally {
      setProcesando("");
    }
  }

  /** Ticket de una venta sin conexión: provisional, o con el e-NCF real (y su QR) ya sincronizada. */
  async function verTicketOffline(v: VentaOffline) {
    let timbre: Timbre | null = null;
    if (v.estado === "sincronizada" && v.facturaId && conexion.enLinea) {
      try {
        await api.firmar(v.facturaId);
        timbre = await solucionesApi.timbre(v.facturaId);
      } catch {
        timbre = null;
      }
    }
    setTicket({ datos: ticketDeVentaOffline(v), timbre });
  }

  function pedirCierre() {
    if (!sesion) return;
    if (porSincronizarCaja > 0) {
      mostrarToast("No se puede cerrar la caja todavía", {
        detalle: `Hay ${porSincronizarCaja} venta(s) sin conexión por sincronizar: sincronícelas antes del cierre para que el arqueo cuadre.`,
        error: true,
      });
      return;
    }
    if (!conexion.enLinea) {
      mostrarToast("No se puede cerrar la caja sin conexión", { detalle: "El cierre se registra en el servidor: espere a que vuelva la conexión.", error: true });
      return;
    }
    setContado({ efectivo: "", tarjeta: "", transferencia: "" });
    setNotasCierre("");
    setCerrar(true);
  }

  async function cancelarPendiente() {
    if (pendiente && !pendiente.enviada) {
      try {
        await api.eliminar(pendiente.facturaId);
      } catch {
        // si ya no se puede borrar, queda como borrador en Facturas
      }
    }
    setPendiente(null);
    setErrorVenta(null);
    setErroresXml([]);
    setCobrando(false);
  }

  async function abrirCaja() {
    setAbriendo(true);
    try {
      const s = await solucionesApi.abrirSesion({
        caja: CAJA_PRINCIPAL,
        fondo_inicial: (Number(apertura.fondo) || 0).toFixed(2),
        cajero: apertura.cajero.trim() || null,
        almacen_id: apertura.almacenId || null,
      });
      setSesion(s);
      setCierre(null);
      window.setTimeout(() => buscador.current?.focus(), 50);
    } catch (err) {
      mostrarToast("No se pudo abrir la caja", { detalle: mensajeError(err), error: true });
    } finally {
      setAbriendo(false);
    }
  }

  async function cerrarCaja() {
    if (!sesion) return;
    if (porSincronizarCaja > 0) {
      setCerrar(false);
      pedirCierre();
      return;
    }
    setCerrando(true);
    try {
      const s = await solucionesApi.cerrarSesion(sesion.id, {
        contado: {
          efectivo: (Number(contado.efectivo) || 0).toFixed(2),
          tarjeta: (Number(contado.tarjeta) || 0).toFixed(2),
          transferencia: (Number(contado.transferencia) || 0).toFixed(2),
        },
        notas: notasCierre.trim() || null,
      });
      setCierre(s);
      setCerrar(false);
      setSesion(null);
      setCarrito([]);
      void actualizarCatalogo({ sesion: null }).catch(() => undefined);
      void conexion.limpiarSincronizadas(sesion.id).catch(() => undefined);
      setHistorial(await solucionesApi.listarSesiones(10).catch(() => historial));
    } catch (err) {
      mostrarToast("No se pudo cerrar la caja", { detalle: mensajeError(err), error: true });
    } finally {
      setCerrando(false);
    }
  }

  function guardarConfig() {
    guardarEmisor(emisor);
    guardarVencimientoSecuencia("31", vence["31"]);
    setConfig(false);
    mostrarToast("Datos de la caja guardados");
  }

  const modalConfig = config && (
    <Modal
      titulo="Configurar caja"
      onCerrar={() => setConfig(false)}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={() => setConfig(false)}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardarConfig}>
            Guardar
          </button>
        </>
      }
    >
      <span className="hint">Los mismos datos del emisor que usa Nueva factura (se guardan en este navegador).</span>
      <label className="fld">
        <span className="lbl">RNC del emisor</span>
        <input className="inp" value={emisor.rnc} onChange={(e) => setEmisor({ ...emisor, rnc: e.target.value })} />
      </label>
      <label className="fld">
        <span className="lbl">Razón social</span>
        <input className="inp" value={emisor.razonSocial} onChange={(e) => setEmisor({ ...emisor, razonSocial: e.target.value })} />
      </label>
      <label className="fld">
        <span className="lbl">Dirección</span>
        <input className="inp" value={emisor.direccion} onChange={(e) => setEmisor({ ...emisor, direccion: e.target.value })} />
      </label>
      <label className="fld">
        <span className="lbl">Secuencia E31 autorizada hasta</span>
        <input className="inp" type="date" value={vence["31"]} onChange={(e) => setVence({ ...vence, "31": e.target.value })} />
        <span className="hint">Para las ventas con RNC (crédito fiscal). El E32 no lleva fecha de vencimiento.</span>
      </label>
    </Modal>
  );

  if (cargando && !sesion && historial.length === 0)
    return (
      <div className="content">
        <p className="pad mut">Cargando…</p>
      </div>
    );

  // --- Sin caja abierta: promoción (primer uso), apertura e historial ---
  if (!sesion) {
    return (
      <div className="content" style={{ maxWidth: 1120 }}>
        {errorCarga && <AvisoError mensaje={errorCarga} onReintentar={cargar} />}
        {verPromo ? (
          <Promo onProbar={() => setVerPromo(false)} onSeguir={() => navigate("/facturas")} />
        ) : (
          <>
            <div className="ph">
              <div>
                <h1>{MARCA} POS</h1>
                <p>Abra la caja para empezar a vender. Cada venta emite su e-CF de consumo (E32) o crédito fiscal (E31) y descuenta el inventario.</p>
              </div>
            </div>
            {cierre && <ResumenCierre s={cierre} />}
            <section className="card" style={{ maxWidth: 560 }}>
              <div className="card-h">
                <h2>Abrir {CAJA_PRINCIPAL.toLowerCase()}</h2>
              </div>
              <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <label className="fld">
                  <span className="lbl">Fondo inicial en efectivo (RD$)</span>
                  <input
                    className="inp"
                    inputMode="decimal"
                    value={apertura.fondo}
                    onChange={(e) => setApertura({ ...apertura, fondo: e.target.value })}
                    placeholder="0.00"
                  />
                </label>
                <label className="fld">
                  <span className="lbl">Cajero</span>
                  <input className="inp" value={apertura.cajero} onChange={(e) => setApertura({ ...apertura, cajero: e.target.value })} />
                </label>
                <label className="fld">
                  <span className="lbl">Almacén de la caja</span>
                  <select className="inp" value={apertura.almacenId} onChange={(e) => setApertura({ ...apertura, almacenId: e.target.value })}>
                    <option value="">Principal</option>
                    {almacenes
                      .filter((a) => !a.es_principal)
                      .map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.nombre}
                        </option>
                      ))}
                  </select>
                </label>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <button type="button" className="btn btn-g btn-sm" onClick={() => setConfig(true)}>
                    Configurar caja
                  </button>
                  <button type="button" className="btn btn-p" onClick={abrirCaja} disabled={abriendo}>
                    {abriendo ? "Abriendo…" : "Abrir caja"}
                  </button>
                </div>
                <span className="hint">
                  Con la caja abierta, el POS sigue vendiendo aunque se caiga la conexión con el servidor: las ventas se guardan en este equipo y
                  sus e-CF se emiten al reconectar. Abrir y cerrar la caja requieren conexión.
                </span>
              </div>
            </section>
            {historial.length > 0 && (
              <section className="card">
                <div className="card-h">
                  <h2>Cierres recientes</h2>
                </div>
                <div className="tw">
                  <table className="tbl" style={{ minWidth: 620 }}>
                    <thead>
                      <tr>
                        <th>Apertura</th>
                        <th>Cajero</th>
                        <th className="num">Ventas</th>
                        <th className="num">Total (RD$)</th>
                        <th className="num">Diferencia efectivo</th>
                        <th>Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {historial.map((h) => (
                        <tr key={h.id}>
                          <td>{new Date(h.abierta_en).toLocaleString("es-DO")}</td>
                          <td>{h.cajero || "—"}</td>
                          <td className="num">{h.cantidad_ventas}</td>
                          <td className="num">{formatoMonto(h.total_ventas)}</td>
                          <td className="num">{h.diferencias ? formatoMonto(h.diferencias.efectivo) : "—"}</td>
                          <td>
                            <span className={h.estado === "abierta" ? "bd b-env" : "bd b-borr"}>
                              {h.estado === "abierta" ? "Abierta" : "Cerrada"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
        {modalConfig}
        {toast}
      </div>
    );
  }

  // --- Caja abierta: venta ---
  const esperado = sesion.esperado;
  const diferencia = (m: MetodoPos) => n2((Number(contado[m]) || 0) - Number(esperado?.[m] ?? 0));
  return (
    <div className="content pos">
      <div className="ph">
        <div>
          <h1>{MARCA} POS</h1>
          <p data-caja-totales>
            {sesion.caja} · abierta {new Date(sesion.abierta_en).toLocaleTimeString("es-DO", { hour: "2-digit", minute: "2-digit" })}
            {sesion.cajero ? ` por ${sesion.cajero}` : ""} · {sesion.cantidad_ventas + (pendientesCaja?.cantidad ?? 0)} venta(s) · RD${" "}
            {formatoMonto(Number(sesion.total_ventas) + (pendientesCaja?.total ?? 0))}
            {pendientesCaja && pendientesCaja.cantidad > 0 ? ` (incluye ${pendientesCaja.cantidad} sin conexión)` : ""}
          </p>
        </div>
        <div className="row">
          <span className={conexion.enLinea ? "pos-conexion on" : "pos-conexion off"} role="status" data-conexion={conexion.enLinea ? "en-linea" : "sin-conexion"}>
            {conexion.enLinea ? "En línea" : "Sin conexión"}
          </span>
          {porSincronizar > 0 && (
            <span className="chip" data-pendientes={porSincronizar}>
              {porSincronizar} por sincronizar
            </span>
          )}
          {(porSincronizar > 0 || !conexion.enLinea) && (
            <button
              type="button"
              className="btn btn-o btn-sm"
              onClick={() => void conexion.sincronizar()}
              disabled={conexion.sincronizando || porSincronizar === 0}
            >
              {conexion.sincronizando ? "Sincronizando…" : "Sincronizar ahora"}
            </button>
          )}
          <button type="button" className="btn btn-o btn-sm" onClick={() => setConfig(true)}>
            Configurar caja
          </button>
          <button
            type="button"
            className="btn btn-o btn-sm"
            onClick={pedirCierre}
            title={porSincronizarCaja > 0 ? "Sincronice las ventas sin conexión antes de cerrar la caja" : undefined}
          >
            Cerrar caja
          </button>
        </div>
      </div>
      {errorCarga && <AvisoError mensaje={errorCarga} />}
      {!conexion.enLinea && (
        <div className="al al-o" role="status">
          <span>
            <b>Sin conexión con el servidor.</b> Puede seguir vendiendo con los precios y la existencia guardados en este equipo: cada venta
            queda guardada y entrega un ticket provisional; su e-CF se emitirá al reconectar.
            {!conexion.almacenamiento && " Este navegador no permite guardar ventas: no podrá vender hasta que vuelva la conexión."}
          </span>
        </div>
      )}
      {conexion.sinSesion && porSincronizar > 0 && (
        <div className="al al-w" role="alert">
          <span>
            <b>Su sesión venció.</b> Inicie sesión de nuevo para sincronizar: las {porSincronizar} venta(s) siguen guardadas en este equipo.
          </span>
          <button type="button" className="btn btn-o btn-sm" onClick={() => navigate("/login", { state: { desde: "/pos" } })}>
            Iniciar sesión
          </button>
        </div>
      )}

      <div className="pos-grid">
        <section className="card pos-prod">
          <div className="pos-busca">
            <label className="tb-search" style={{ maxWidth: "none", flex: 1 }}>
              <IconoBuscar />
              <input
                ref={buscador}
                className="inp"
                placeholder="Buscar por nombre, SKU o código de barras"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && buscarEnter()}
                aria-label="Buscar producto"
              />
            </label>
          </div>
          {categorias.length > 0 && (
            <div className="pos-cats">
              <button type="button" className={categoria ? "chip" : "chip on"} onClick={() => setCategoria("")}>
                Todos
              </button>
              {categorias.map((c) => (
                <button key={c} type="button" className={categoria === c ? "chip on" : "chip"} onClick={() => setCategoria(c)}>
                  {c}
                </button>
              ))}
            </div>
          )}
          <div className="pos-cards">
            {visibles.map((p) => {
              const d = disponible(p);
              const agotado = d !== Infinity && d - enCarrito(p.id) <= 0;
              return (
                <button key={p.id} type="button" className="pos-card" onClick={() => agregar(p)} disabled={agotado} data-producto={p.sku}>
                  <span className="pos-card-cat">{p.categoria || (p.tipo === "servicio" ? "Servicio" : "Producto")}</span>
                  <b>{p.nombre}</b>
                  <span className="pos-card-precio">RD$ {formatoMonto(p.precio_venta)}</span>
                  <span className="hint">
                    {p.tipo === "servicio" ? "Servicio" : agotado ? "Sin existencia" : `Existencia: ${d.toLocaleString("en-US")}`}
                    {p.tasa_itbis === "exento" ? " · Exento" : ` · ITBIS ${p.tasa_itbis}%`}
                  </span>
                </button>
              );
            })}
            {visibles.length === 0 && (
              <p className="mut" style={{ gridColumn: "1 / -1" }}>
                No hay productos que coincidan.
              </p>
            )}
          </div>
        </section>

        <section className="card pos-cart" aria-label="Venta actual">
          <div className="card-h">
            <h2>Venta actual</h2>
            <span className="chip">{tipoEcf === "31" ? "Crédito fiscal · E31" : "Consumo · E32"}</span>
          </div>
          <div className="pos-cart-b">
            <div className="pos-cliente">
              <label className="fld" style={{ flex: 1 }}>
                <span className="lbl">RNC o cédula (opcional)</span>
                <input
                  className="inp inp-sm"
                  inputMode="numeric"
                  value={cliente.rnc}
                  onChange={(e) => setCliente({ rnc: e.target.value, nombre: "" })}
                  onBlur={() => cliente.rnc && !cliente.nombre && verificarCliente()}
                  placeholder="Consumidor final"
                />
              </label>
              {cliente.rnc.replace(/\D/g, "") && (
                <label className="fld" style={{ flex: 1.4 }}>
                  <span className="lbl">Razón social</span>
                  <input className="inp inp-sm" value={cliente.nombre} onChange={(e) => setCliente({ ...cliente, nombre: e.target.value })} />
                </label>
              )}
              {verificando && <span className="hint">Verificando…</span>}
            </div>
            {carrito.length === 0 ? (
              <p className="mut pos-vacio">Toque un producto para agregarlo.</p>
            ) : (
              <ul className="pos-lineas">
                {carrito.map((l) => (
                  <li key={l.producto.id}>
                    <span className="pos-linea-n">
                      <b>{l.producto.nombre}</b>
                      <span className="hint">RD$ {formatoMonto(l.producto.precio_venta)} c/u</span>
                    </span>
                    <span className="pos-qty">
                      <button
                        type="button"
                        onClick={() => cambiarCantidad(l.producto.id, l.cantidad - 1)}
                        aria-label={`Quitar uno de ${l.producto.nombre}`}
                      >
                        −
                      </button>
                      <span>{l.cantidad}</span>
                      <button
                        type="button"
                        onClick={() => cambiarCantidad(l.producto.id, l.cantidad + 1)}
                        aria-label={`Agregar uno de ${l.producto.nombre}`}
                      >
                        +
                      </button>
                    </span>
                    <span className="num">{formatoMonto(l.cantidad * Number(l.producto.precio_venta))}</span>
                  </li>
                ))}
              </ul>
            )}
            <dl className="pos-tot">
              <dt>Subtotal</dt>
              <dd>{formatoMonto(totales.subtotal - totales.descuentos)}</dd>
              <dt>ITBIS</dt>
              <dd>{formatoMonto(totales.i18 + totales.i16)}</dd>
              <dt className="pos-total">Total</dt>
              <dd className="pos-total" data-total>
                RD$ {formatoMonto(total)}
              </dd>
            </dl>
            <div className="row">
              <button type="button" className="btn btn-o" onClick={() => setCarrito([])} disabled={carrito.length === 0}>
                Vaciar
              </button>
              <button type="button" className="btn btn-p pos-cobrar" onClick={abrirCobro} disabled={carrito.length === 0}>
                Cobrar RD$ {formatoMonto(total)}
              </button>
            </div>
          </div>
        </section>
      </div>

      {conexion.cola.length > 0 && (
        <section className="card" aria-label="Ventas sin conexión" data-cola-offline>
          <div className="card-h">
            <h2>Ventas sin conexión</h2>
            <span className="hint">
              Se emiten en orden al reconectar. Las sincronizadas quedan aquí para reimprimir el ticket con su e-NCF hasta cerrar la caja.
            </span>
          </div>
          <div className="tw">
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>Hora</th>
                  <th>Referencia</th>
                  <th className="num">Total (RD$)</th>
                  <th>Estado</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {conexion.cola.map((v) => (
                  <tr key={v.clave} data-venta-offline={v.estado}>
                    <td>{new Date(v.creadaEn).toLocaleTimeString("es-DO", { hour: "2-digit", minute: "2-digit" })}</td>
                    <td className="mono">{v.encf ?? referenciaVenta(v.clave)}</td>
                    <td className="num">{formatoMonto(v.total)}</td>
                    <td>
                      {v.estado === "sincronizada" ? (
                        <span className="bd b-acep">{v.contingencia ? "Emitida en contingencia" : "Emitida"}</span>
                      ) : v.estado === "error" ? (
                        <>
                          <span className="bd b-rech">Error</span>
                          <span className="row-motivo">{v.error}</span>
                          {v.erroresXml.length > 0 && <span className="row-motivo">{v.erroresXml.join(" · ")}</span>}
                        </>
                      ) : (
                        <span className="bd b-env">{conexion.sincronizando ? "Sincronizando…" : "Pendiente"}</span>
                      )}
                    </td>
                    <td className="num">
                      <div className="row" style={{ justifyContent: "flex-end" }}>
                        {v.estado === "error" && (
                          <button
                            type="button"
                            className="btn btn-o btn-sm"
                            onClick={() => void conexion.sincronizar()}
                            disabled={conexion.sincronizando || !conexion.enLinea}
                          >
                            Reintentar
                          </button>
                        )}
                        <button type="button" className="btn btn-g btn-sm" onClick={() => void verTicketOffline(v)}>
                          {v.estado === "sincronizada" ? "Ticket con e-NCF" : "Ticket provisional"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {cobrando && (
        <Modal
          titulo={`Cobrar RD$ ${formatoMonto(total)}`}
          onCerrar={() => !procesando && (pendiente ? undefined : setCobrando(false))}
          ancho={560}
          pie={
            pendiente ? (
              <>
                {!pendiente.enviada && (
                  <button type="button" className="btn btn-o" onClick={cancelarPendiente} disabled={!!procesando}>
                    Cancelar venta
                  </button>
                )}
                <button type="button" className="btn btn-p" onClick={emitir} disabled={!!procesando}>
                  {procesando || (pendiente.enviada ? "Registrar en caja" : "Reintentar envío")}
                </button>
              </>
            ) : (
              <>
                <button type="button" className="btn btn-o" onClick={() => setCobrando(false)} disabled={!!procesando}>
                  Cancelar
                </button>
                <button type="button" className="btn btn-p" onClick={emitir} disabled={!!procesando || !!resultadoPagos.error}>
                  {procesando || (conexion.enLinea ? "Cobrar y emitir e-CF" : "Cobrar sin conexión")}
                </button>
              </>
            )
          }
        >
          {!pendiente && (
            <>
              <div className="seg" role="group" aria-label="Forma de pago">
                {([...METODOS, { value: "mixto", label: "Mixto" }] as { value: ModoPago; label: string }[]).map((m) => (
                  <button key={m.value} type="button" className={modo === m.value ? "on" : ""} onClick={() => setModo(m.value)}>
                    {m.label}
                  </button>
                ))}
              </div>
              {modo === "efectivo" && (
                <div className="fld">
                  <label className="lbl" htmlFor="recibido">
                    Recibido (RD$)
                  </label>
                  <input
                    id="recibido"
                    className="inp pos-recibido"
                    inputMode="decimal"
                    value={montos.efectivo}
                    onChange={(e) => setMontos({ ...montos, efectivo: e.target.value })}
                    autoFocus
                  />
                  <div className="row">
                    {[total, 500, 1000, 2000]
                      .filter((v, i) => i === 0 || v > total)
                      .map((v, i) => (
                        <button key={v} type="button" className="btn btn-o btn-sm" onClick={() => setMontos({ ...montos, efectivo: v.toFixed(2) })}>
                          {i === 0 ? "Exacto" : formatoMonto(v)}
                        </button>
                      ))}
                  </div>
                </div>
              )}
              {(modo === "tarjeta" || modo === "transferencia") && (
                <label className="fld">
                  <span className="lbl">{modo === "tarjeta" ? "Número de aprobación (opcional)" : "Referencia de la transferencia (opcional)"}</span>
                  <input className="inp" value={referencia} onChange={(e) => setReferencia(e.target.value)} />
                </label>
              )}
              {modo === "mixto" && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
                  {METODOS.map((m) => (
                    <label key={m.value} className="fld">
                      <span className="lbl">{m.label}</span>
                      <input
                        className="inp"
                        inputMode="decimal"
                        value={montos[m.value]}
                        onChange={(e) => setMontos({ ...montos, [m.value]: e.target.value })}
                        aria-label={`Monto en ${m.label}`}
                      />
                    </label>
                  ))}
                </div>
              )}
              <dl className="kv" style={{ gridTemplateColumns: "1fr auto" }}>
                <dt>Recibido</dt>
                <dd className="num">RD$ {formatoMonto(resultadoPagos.recibido)}</dd>
                <dt style={{ fontWeight: 650, color: "#111827" }}>Cambio</dt>
                <dd className="num pos-cambio" data-cambio>
                  RD$ {formatoMonto(resultadoPagos.cambio)}
                </dd>
              </dl>
              {resultadoPagos.error && (
                <span className="hint" style={{ color: "#B91C1C" }}>
                  {resultadoPagos.error}
                </span>
              )}
            </>
          )}
          {pendiente && (
            <div className="al al-w">
              <span>
                <b>Venta {pendiente.ticket.encf} sin terminar.</b>{" "}
                {pendiente.enviada
                  ? "El e-CF ya llegó a la DGII; falta registrarlo en la caja."
                  : "El e-CF quedó guardado pero no se envió a la DGII. Reintente el envío o cancele la venta."}
              </span>
            </div>
          )}
          {!pendiente && !conexion.enLinea && (
            <span className="hint">Sin conexión: la venta se guarda en este equipo y su e-CF se emite al reconectar (ticket provisional).</span>
          )}
          {errorVenta && <AvisoError mensaje={errorVenta} />}
          {erroresXml.length > 0 && (
            <ul className="row-motivo" style={{ maxWidth: "none", margin: 0, paddingLeft: 18 }}>
              {erroresXml.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </Modal>
      )}

      {ticket && (
        <Modal
          titulo={ticket.datos.provisional ? "Venta guardada sin conexión" : `Venta ${ticket.datos.encf} emitida`}
          onCerrar={() => setTicket(null)}
          ancho={420}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={imprimirTicket}>
                Imprimir ticket
              </button>
              <button
                type="button"
                className="btn btn-p"
                onClick={() => {
                  setTicket(null);
                  window.setTimeout(() => buscador.current?.focus(), 50);
                }}
              >
                Nueva venta
              </button>
            </>
          }
        >
          {ticket.datos.cambio > 0 && (
            <div className="al al-s">
              <span>
                <b>Cambio: RD$ {formatoMonto(ticket.datos.cambio)}</b>
              </span>
            </div>
          )}
          {ticket.datos.provisional && (
            <div className="al al-o" role="status">
              <span>
                <b>Venta guardada sin conexión.</b> Entregue el ticket provisional: el e-CF se emitirá automáticamente al reconectar y podrá
                reimprimir el ticket con su e-NCF desde «Ventas sin conexión».
              </span>
            </div>
          )}
          {ticket.datos.contingencia && (
            <div className="al al-o" role="status">
              <span>
                <b>Emitida en contingencia.</b> La DGII no respondió: entregue el ticket (lleva la leyenda de contingencia); el e-CF se enviará
                automáticamente al restablecerse la conexión.
              </span>
            </div>
          )}
          {ticket.datos.estadoDgii === "Rechazado" && (
            <div className="al al-d">
              <span>La DGII rechazó el comprobante: revíselo en Facturas de venta.</span>
            </div>
          )}
          <Ticket t={ticket.datos} timbre={ticket.timbre} />
        </Modal>
      )}

      {cerrar && esperado && (
        <Modal
          titulo={`Cerrar ${sesion.caja.toLowerCase()}`}
          onCerrar={() => setCerrar(false)}
          ancho={560}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setCerrar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={cerrarCaja} disabled={cerrando}>
                {cerrando ? "Cerrando…" : "Cerrar caja"}
              </button>
            </>
          }
        >
          <span className="hint">
            Cuente lo que hay en caja por forma de pago. Esperado = fondo de RD$ {formatoMonto(sesion.fondo_inicial)} + cobros − cambio entregado.
          </span>
          <table className="tbl pos-arqueo">
            <thead>
              <tr>
                <th>Forma de pago</th>
                <th className="num">Esperado</th>
                <th className="num">Contado</th>
                <th className="num">Diferencia</th>
              </tr>
            </thead>
            <tbody>
              {METODOS.map((m) => (
                <tr key={m.value}>
                  <td>{m.label}</td>
                  <td className="num">{formatoMonto(esperado[m.value])}</td>
                  <td className="num">
                    <input
                      className="inp inp-sm"
                      inputMode="decimal"
                      value={contado[m.value]}
                      onChange={(e) => setContado({ ...contado, [m.value]: e.target.value })}
                      aria-label={`Contado en ${m.label}`}
                      style={{ textAlign: "right", maxWidth: 130 }}
                    />
                  </td>
                  <td className={diferencia(m.value) < 0 ? "num pos-falta" : "num"}>{formatoMonto(diferencia(m.value))}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <label className="fld">
            <span className="lbl">Notas del cierre</span>
            <input className="inp" value={notasCierre} onChange={(e) => setNotasCierre(e.target.value)} />
          </label>
        </Modal>
      )}
      {modalConfig}
      {toast}
    </div>
  );
}

/** Imprime solo el ticket (80 mm): la clase en <body> activa el CSS de impresión de pos.css. */
function imprimirTicket() {
  document.body.classList.add("pos-imprimir");
  const quitar = () => {
    document.body.classList.remove("pos-imprimir");
    window.removeEventListener("afterprint", quitar);
  };
  window.addEventListener("afterprint", quitar);
  window.print();
}

function ResumenCierre({ s }: { s: SesionCaja }) {
  const filas: MetodoPos[] = ["efectivo", "tarjeta", "transferencia"];
  return (
    <section className="card" data-cierre>
      <div className="card-h">
        <h2>Caja cerrada · {fechaLegible(s.cerrada_en)}</h2>
        <span className="hint">
          {s.cantidad_ventas} venta(s) · RD$ {formatoMonto(s.total_ventas)}
        </span>
      </div>
      <div className="tw">
        <table className="tbl">
          <thead>
            <tr>
              <th>Forma de pago</th>
              <th className="num">Esperado</th>
              <th className="num">Contado</th>
              <th className="num">Diferencia</th>
            </tr>
          </thead>
          <tbody>
            {filas.map((m) => (
              <tr key={m}>
                <td>{m === "efectivo" ? "Efectivo" : m === "tarjeta" ? "Tarjeta" : "Transferencia"}</td>
                <td className="num">{formatoMonto(s.esperado?.[m])}</td>
                <td className="num">{formatoMonto(s.contado?.[m])}</td>
                <td className={Number(s.diferencias?.[m]) < 0 ? "num pos-falta" : "num"}>{formatoMonto(s.diferencias?.[m])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.notas_cierre && (
        <p className="pad mut" style={{ margin: 0 }}>
          {s.notas_cierre}
        </p>
      )}
      {s.cajero && (
        <p className="pad hint" style={{ margin: 0 }}>
          Cajero: {s.cajero}
        </p>
      )}
    </section>
  );
}
