// Formatos de envío de datos 606 (compras), 607 (ventas) y 608 (anulados), según
// Formatos.dc.html del prototipo. El 606 usa el servicio formato_606 existente (y
// enlaza a sus pantallas de clasificación); el 607 y el 608 vienen de erp-api.
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { formato606Api } from "../../lib/formato606Api";
import { impuestosApi } from "../../lib/impuestosApi";
import type { Declaracion, Fila608, Formato607, Formato608, MarcarPresentadaPayload, TipoAnulacion } from "../../lib/impuestosApi";
import { EMPRESA_RNC, formatoMonto } from "../../lib/marca";
import type { Registro606 } from "../../lib/types";
import { AvisoError, Cargando, Estadistica, Modal, Vacio } from "../ingresos/IngresosShell";
import { descargarCsv, mensajeError } from "../ingresos/ingresosUtil";
import { useAviso } from "../ingresos/useAviso";
import { Advertencias, EstadoDeclaracionBadge, IconoDescargar, IconoOk, IconoValidar, ModalPresentada, SelectorPeriodo } from "./ImpuestosShell";
import {
  descargarTexto,
  etiquetaPeriodo,
  fechaDgii,
  nombrePeriodo,
  periodosRecientes,
  textoVencimiento,
  useDeclaraciones,
  usePeriodoMensual,
} from "./impuestosUtil";

type Formato = "606" | "607" | "608";
type Avisar = (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;

const PESTANAS: { id: Formato; label: string; sub: string }[] = [
  { id: "606", label: "Formato 606", sub: "Compras" },
  { id: "607", label: "Formato 607", sub: "Ventas" },
  { id: "608", label: "Formato 608", sub: "Anulados" },
];

/** Renglones que se muestran de entrada; el resto con "Ver todos" (el 607 puede tener miles). */
const LIMITE_FILAS = 200;

const suma = <T,>(filas: T[], campo: (f: T) => string | null | undefined) => filas.reduce((s, f) => s + (Number(campo(f)) || 0), 0);
const rd = (n: number | string) => `RD$ ${formatoMonto(n)}`;
const registros = (n: number) => `${n.toLocaleString("en-US")} ${n === 1 ? "registro" : "registros"}`;

function nombreArchivo(formato: Formato, rnc: string, periodo: string) {
  return `DGII_F_${formato}_${(rnc || EMPRESA_RNC || "<RNC>").replace(/\D/g, "") || "<RNC>"}_${periodo}.TXT`;
}

export function FormatosPage() {
  const { formato: param } = useParams<{ formato: string }>();
  const navigate = useNavigate();
  const [periodo, setPeriodo] = usePeriodoMensual();
  const [toast, mostrar] = useAviso();
  const [modal, setModal] = useState(false);
  const hoy = new Date().getFullYear();
  const { buscar, actualizar } = useDeclaraciones([String(hoy), String(hoy - 1), periodo.slice(0, 4)]);

  const formato = PESTANAS.find((p) => p.id === param)?.id;
  const opciones = useMemo(() => {
    const lista = periodosRecientes();
    if (!lista.includes(periodo)) lista.push(periodo);
    return lista.map((p) => ({ valor: p, label: etiquetaPeriodo(nombrePeriodo(p, true), formato ? buscar(formato, p) : null) }));
  }, [periodo, formato, buscar]);

  if (!formato) return <Navigate to="/impuestos/formatos/606" replace />;
  const declaracion = buscar(formato, periodo);
  const presentada = declaracion?.estado === "presentada";

  async function marcar(payload: MarcarPresentadaPayload) {
    const d = await impuestosApi.marcarPresentada(formato!, periodo, payload);
    actualizar(d);
    setModal(false);
    mostrar(`Formato ${formato} marcado como presentado`, { detalle: nombrePeriodo(periodo, true) });
  }

  async function desmarcar() {
    try {
      actualizar(await impuestosApi.desmarcarPresentada(formato!, periodo));
      mostrar(`Formato ${formato} vuelve a estar pendiente`, { detalle: nombrePeriodo(periodo, true) });
    } catch (err) {
      mostrar("No se pudo desmarcar la declaración", { detalle: mensajeError(err), error: true });
    }
  }

  const props = { periodo, declaracion, mostrar };
  return (
    <div className="content">
      <div className="ph">
        <div>
          <div className="crumb">
            <Link to="/impuestos/formatos/606">Impuestos</Link>
            <span>/</span>
            <span>Formatos de envío de datos</span>
          </div>
          <h1>Formatos 606 · 607 · 608</h1>
          <p>Prepare, valide y genere los archivos TXT para la Oficina Virtual de la DGII</p>
        </div>
        <div className="row imp-acciones">
          <SelectorPeriodo
            valor={periodo}
            opciones={opciones}
            onCambiar={setPeriodo}
            hint={
              declaracion && (
                <>
                  <EstadoDeclaracionBadge declaracion={declaracion} /> {textoVencimiento(declaracion.vence, declaracion)}
                </>
              )
            }
          />
          {presentada ? (
            <button type="button" className="btn btn-o" onClick={desmarcar}>
              Desmarcar presentada
            </button>
          ) : (
            <button type="button" className="btn btn-o" onClick={() => setModal(true)}>
              Marcar como presentada
            </button>
          )}
        </div>
      </div>

      <div className="tabs" role="tablist" aria-label="Formato">
        {PESTANAS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={formato === t.id}
            className={formato === t.id ? "tab on" : "tab"}
            onClick={() => navigate(`/impuestos/formatos/${t.id}?periodo=${periodo}`)}
          >
            <span>{t.label}</span>
            <span className="hint">{t.sub}</span>
          </button>
        ))}
      </div>

      {formato === "606" && <Formato606Vista key={periodo} {...props} />}
      {formato === "607" && <Formato607Vista key={periodo} {...props} />}
      {formato === "608" && <Formato608Vista key={periodo} {...props} />}

      {modal && (
        <ModalPresentada titulo={`Formato ${formato} de ${nombrePeriodo(periodo)} presentado`} onCerrar={() => setModal(false)} onConfirmar={marcar} />
      )}
      {toast}
    </div>
  );
}

interface VistaProps {
  periodo: string;
  declaracion: Declaracion | null;
  mostrar: Avisar;
}

/** Tarjeta "Fecha límite" con el estado de la declaración del período. */
function TarjetaVence({ declaracion }: { declaracion: Declaracion | null }) {
  return (
    <div className="card stat">
      <span className="k">
        Fecha límite <EstadoDeclaracionBadge declaracion={declaracion} />
      </span>
      <span className="v" style={{ fontSize: 21 }}>
        {declaracion ? fechaDgii(declaracion.vence) : "—"}
      </span>
      <span className="s">{declaracion ? textoVencimiento(declaracion.vence, declaracion) : "Calendario fiscal no disponible"}</span>
    </div>
  );
}

function Tarjetas({ children }: { children: ReactNode }) {
  return <div className="imp-stats">{children}</div>;
}

interface ErrorFila {
  clave: string;
  linea: number | null;
  mensaje: string;
  accion?: { label: string; to: string };
}

interface Validacion {
  errores: ErrorFila[];
  ok: string;
}

/**
 * Tarjeta de vista previa del formato: Validar → errores o validación correcta → Generar TXT,
 * como el prototipo (la generación se habilita después de validar sin errores).
 */
function TarjetaFormato({
  titulo,
  sub,
  acciones,
  validar,
  generar,
  exportar,
  genLabel = "Generar archivo TXT",
  bloqueo,
  version,
  mostrar,
  children,
}: {
  titulo: string;
  sub: string;
  acciones?: ReactNode;
  validar: () => Promise<Validacion>;
  generar: () => Promise<{ nombre: string; contenido: string | Blob }>;
  exportar?: () => void;
  genLabel?: string;
  /** Motivo adicional que impide generar (p. ej. 608 sin confirmar la declaración en cero). */
  bloqueo?: string | null;
  /** Cambia cuando se modifican los datos: obliga a validar de nuevo. */
  version?: number;
  mostrar: Avisar;
  children: ReactNode;
}) {
  const [validacion, setValidacion] = useState<Validacion | null>(null);
  const [validando, setValidando] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setValidacion(null);
    setError(null);
  }, [version]);

  async function onValidar() {
    setValidando(true);
    setError(null);
    try {
      setValidacion(await validar());
    } catch (err) {
      setValidacion(null);
      setError(`No se pudo validar el formato. ${mensajeError(err)}`);
    } finally {
      setValidando(false);
    }
  }

  async function onGenerar() {
    setGenerando(true);
    setError(null);
    try {
      const { nombre, contenido } = await generar();
      descargarTexto(nombre, contenido);
      mostrar("Archivo generado", { detalle: nombre });
    } catch (err) {
      setError(`No se pudo generar el archivo. ${mensajeError(err)}`);
    } finally {
      setGenerando(false);
    }
  }

  const errores = validacion?.errores ?? [];
  const motivo = !validacion
    ? "Primero valide el formato para detectar errores antes de exportar."
    : errores.length > 0
      ? "Corrija los errores señalados para habilitar la generación."
      : (bloqueo ?? null);

  return (
    <section className="card">
      <div className="card-h">
        <div>
          <h2>{titulo}</h2>
          <span className="hint">{sub}</span>
        </div>
        <div className="row">
          {acciones}
          {exportar && (
            <button type="button" className="btn btn-o btn-sm" onClick={exportar}>
              Exportar CSV
            </button>
          )}
          <button type="button" className="btn btn-o btn-sm" onClick={onValidar} disabled={validando}>
            <IconoValidar />
            {validando ? "Validando…" : "Validar"}
          </button>
          {motivo ? (
            <span className="tip r" data-tip={motivo}>
              <button type="button" className="btn btn-p btn-sm" disabled>
                <IconoDescargar />
                {genLabel}
              </button>
            </span>
          ) : (
            <button type="button" className="btn btn-p btn-sm" onClick={onGenerar} disabled={generando}>
              <IconoDescargar />
              {generando ? "Generando…" : genLabel}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="imp-pad">
          <AvisoError mensaje={error} />
        </div>
      )}
      {validacion && errores.length > 0 && (
        <div className="imp-pad">
          <div className="al al-d" style={{ flexDirection: "column", gap: 10 }} role="alert">
            <b>
              Se encontraron {errores.length} {errores.length === 1 ? "error" : "errores"}. Corríjalos antes de generar el archivo.
            </b>
            {errores.map((e) => (
              <div key={e.clave} className="imp-error">
                {e.linea != null && <span className="mono imp-error-l">Línea {e.linea}</span>}
                <span className="imp-error-m">{e.mensaje}</span>
                {e.accion && (
                  <Link className="btn btn-o btn-sm" to={e.accion.to}>
                    {e.accion.label}
                  </Link>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {validacion && errores.length === 0 && (
        <div className="imp-pad">
          <div className="al al-s" role="status">
            <IconoOk />
            <span>
              <b>Validación correcta.</b> {validacion.ok}
            </span>
          </div>
        </div>
      )}
      {children}
    </section>
  );
}

/** Pie de la tabla: cuántos renglones se ven, "Ver todos" y el nombre del archivo. */
function PieTabla({ mostrando, total, archivo, nota, onVerTodos }: { mostrando: number; total: number; archivo: string; nota?: string; onVerTodos: () => void }) {
  return (
    <div className="imp-pie">
      {mostrando < total ? `Mostrando ${mostrando.toLocaleString("en-US")} de ${registros(total)}` : registros(total)}
      {mostrando < total && (
        <>
          {" "}
          <button type="button" className="btn btn-g btn-sm" onClick={onVerTodos}>
            Ver todos
          </button>
        </>
      )}
      {nota && ` · ${nota}`} · Archivo: <span className="mono">{archivo}</span>
    </div>
  );
}

// --- 606 · Compras (servicio formato_606) ------------------------------------

const clasificado = (r: Registro606) => r.tipo_bienes_servicios != null && r.forma_pago != null;
const autoemitido = (r: Registro606) => /^E4[13]/.test(r.ncf);

function Formato606Vista({ periodo, mostrar }: VistaProps) {
  const [filas, setFilas] = useState<Registro606[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conError, setConError] = useState<Set<string>>(new Set());
  const [todos, setTodos] = useState(false);

  const cargar = useCallback(async () => {
    setError(null);
    try {
      setFilas(await formato606Api.listarRegistros(periodo));
    } catch (err) {
      setFilas(null);
      setError(mensajeError(err));
    }
  }, [periodo]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  if (error) return <AvisoError titulo="No se pudo cargar el formato 606." mensaje={error} onReintentar={() => void cargar()} />;
  if (!filas) return <Cargando />;

  const pendientes = filas.filter((r) => !clasificado(r)).length;
  const auto = filas.filter(autoemitido).length;
  const itbisRet = suma(filas, (r) => r.itbis_retenido);
  const isrRet = suma(filas, (r) => r.monto_retencion_renta);
  const visibles = todos ? filas : filas.slice(0, LIMITE_FILAS);
  const clasificar = `/formato-606/periodos/${periodo}`;

  async function validar(): Promise<Validacion> {
    const r = await formato606Api.validarPeriodo(periodo);
    const lista = filas ?? [];
    setConError(new Set(r.errores.map((e) => e.registro_id)));
    return {
      errores: r.errores.map((e, i) => {
        const idx = lista.findIndex((f) => f.id === e.registro_id);
        return {
          clave: `${e.registro_id}-${i}`,
          linea: idx >= 0 ? idx + 1 : null,
          mensaje: `${e.ncf}: ${e.mensaje}`,
          accion: { label: "Corregir", to: `/formato-606/registros/${e.registro_id}/editar` },
        };
      }),
      ok: `${registros(r.total_registros)} listos para enviar.`,
    };
  }

  async function generar() {
    const { nombreArchivo: nombre, contenido } = await formato606Api.generarArchivo(periodo);
    return { nombre, contenido };
  }

  function exportar() {
    descargarCsv(`Formato_606_${periodo}.csv`, [
      ["RNC / Cédula", "Tipo id.", "NCF", "NCF modificado", "Tipo bien/serv.", "Fecha comprobante", "Fecha pago", "Servicios", "Bienes", "Monto facturado", "ITBIS facturado", "ITBIS retenido", "ISR retenido", "Forma de pago"],
      ...filas!.map((r) => [
        r.rnc_cedula,
        r.tipo_id,
        r.ncf,
        r.ncf_modificado,
        r.tipo_bienes_servicios != null ? String(r.tipo_bienes_servicios).padStart(2, "0") : "",
        fechaDgii(r.fecha_comprobante),
        fechaDgii(r.fecha_pago),
        Number(r.monto_servicios).toFixed(2),
        Number(r.monto_bienes).toFixed(2),
        Number(r.monto_facturado).toFixed(2),
        Number(r.itbis_facturado).toFixed(2),
        Number(r.itbis_retenido).toFixed(2),
        Number(r.monto_retencion_renta).toFixed(2),
        r.forma_pago != null ? String(r.forma_pago).padStart(2, "0") : "",
      ]),
    ]);
  }

  return (
    <>
      <Tarjetas>
        <Estadistica k="Registros" v={filas.length.toLocaleString("en-US")} s={`${filas.length - auto} con e-NCF · ${auto} con E41/E43`} />
        <Estadistica k="Monto facturado" v={rd(suma(filas, (r) => r.monto_facturado))} s="Bienes y servicios" />
        <Estadistica k="ITBIS facturado" v={rd(suma(filas, (r) => r.itbis_facturado))} s="Adelantado en compras" />
        <Estadistica k="Retenciones" v={rd(itbisRet + isrRet)} s={`ITBIS ${formatoMonto(itbisRet)} · ISR ${formatoMonto(isrRet)}`} />
      </Tarjetas>

      {pendientes > 0 && (
        <div className="al al-w">
          <IconAlerta />
          <span style={{ flex: 1 }}>
            <b>
              {pendientes} {pendientes === 1 ? "compra sin clasificar" : "compras sin clasificar"}.
            </b>{" "}
            Indique el tipo de bien o servicio y la forma de pago antes de generar el 606.
          </span>
          <Link className="btn btn-o btn-sm" to={clasificar}>
            Clasificar compras
          </Link>
        </div>
      )}

      <TarjetaFormato
        titulo="Vista previa · 606 Compras de bienes y servicios"
        sub={`${registros(filas.length)} · incluye compras pagadas en el período`}
        acciones={
          <Link className="btn btn-g btn-sm" to={clasificar}>
            Ver y clasificar renglones
          </Link>
        }
        validar={validar}
        generar={generar}
        exportar={filas.length > 0 ? exportar : undefined}
        mostrar={mostrar}
      >
        {filas.length === 0 ? (
          <Vacio
            titulo={`Sin compras registradas en ${nombrePeriodo(periodo)}`}
            texto="Los renglones se agregan solos al recibir e-CF de proveedores y al emitir comprobantes de compras, gastos menores o pagos al exterior. También puede agregar registros manuales."
          >
            <Link className="btn btn-o btn-sm" to={`/formato-606/periodos/${periodo}/registros/nuevo`}>
              + Registro manual
            </Link>
          </Vacio>
        ) : (
          <>
            <div className="tw" style={{ marginTop: 12 }}>
              <table className="tbl" style={{ minWidth: 1100 }}>
                <thead>
                  <tr>
                    <th>RNC / Cédula</th>
                    <th>Tipo id.</th>
                    <th>NCF</th>
                    <th>Tipo bien/serv.</th>
                    <th>Fecha comp.</th>
                    <th>Fecha pago</th>
                    <th className="num">Servicios</th>
                    <th className="num">Bienes</th>
                    <th className="num">ITBIS facturado</th>
                    <th className="num">ITBIS retenido</th>
                    <th className="num">ISR retenido</th>
                  </tr>
                </thead>
                <tbody>
                  {visibles.map((r) => (
                    <tr key={r.id} className={conError.has(r.id) ? "imp-fila-error" : undefined}>
                      <td className="mono">{r.rnc_cedula}</td>
                      <td className="mono">{r.tipo_id}</td>
                      <td className="mono">{r.ncf}</td>
                      <td>
                        {r.tipo_bienes_servicios != null ? (
                          String(r.tipo_bienes_servicios).padStart(2, "0")
                        ) : (
                          <Link to={`/formato-606/registros/${r.id}/editar`} className="bd b-pend" title="Clasificar este renglón">
                            Sin clasificar
                          </Link>
                        )}
                      </td>
                      <td>{fechaDgii(r.fecha_comprobante)}</td>
                      <td>{fechaDgii(r.fecha_pago)}</td>
                      <td className="num">{formatoMonto(r.monto_servicios)}</td>
                      <td className="num">{formatoMonto(r.monto_bienes)}</td>
                      <td className="num">{formatoMonto(r.itbis_facturado)}</td>
                      <td className="num">{formatoMonto(r.itbis_retenido)}</td>
                      <td className="num">{formatoMonto(r.monto_retencion_renta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <PieTabla mostrando={visibles.length} total={filas.length} archivo={nombreArchivo("606", EMPRESA_RNC, periodo)} onVerTodos={() => setTodos(true)} />
          </>
        )}
      </TarjetaFormato>
    </>
  );
}

// --- 607 · Ventas (erp-api) ---------------------------------------------------

function Formato607Vista({ periodo, mostrar }: VistaProps) {
  const [datos, setDatos] = useState<Formato607 | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [todos, setTodos] = useState(false);

  const cargar = useCallback(async () => {
    setError(null);
    try {
      setDatos(await impuestosApi.formato607(periodo));
    } catch (err) {
      setDatos(null);
      setError(mensajeError(err));
    }
  }, [periodo]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  if (error) return <AvisoError titulo="No se pudo cargar el formato 607." mensaje={error} onReintentar={() => void cargar()} />;
  if (!datos) return <Cargando />;

  const { filas, totales } = datos;
  const formas = [
    { k: "Efectivo", v: suma(filas, (f) => f.efectivo) },
    { k: "Tarjeta débito/crédito", v: suma(filas, (f) => f.tarjeta) },
    { k: "Cheque / transferencia", v: suma(filas, (f) => f.cheque_transferencia) },
    { k: "Venta a crédito", v: suma(filas, (f) => f.venta_credito) },
  ];
  const otras = suma(filas, (f) => f.bonos_certificados) + suma(filas, (f) => f.permuta) + suma(filas, (f) => f.otras_formas);
  const totalFormas = formas.reduce((s, f) => s + f.v, 0) + otras;
  const pct = (v: number) => `${totalFormas > 0 ? ((v / totalFormas) * 100).toFixed(1) : "0.0"}% de las ventas`;
  const visibles = todos ? filas : filas.slice(0, LIMITE_FILAS);

  async function validar(): Promise<Validacion> {
    const d = await impuestosApi.formato607(periodo);
    setDatos(d);
    return {
      errores: [],
      ok: `${registros(d.totales.cantidad)} listos. Total facturado RD$ ${formatoMonto(d.totales.monto_facturado)} · ITBIS RD$ ${formatoMonto(d.totales.itbis_facturado)}.${
        d.advertencias.length > 0 ? " Revise las advertencias antes de enviarlo." : ""
      }`,
    };
  }

  function exportar() {
    descargarCsv(`Formato_607_${periodo}.csv`, [
      ["RNC / Cédula", "Tipo id.", "e-NCF", "NCF modificado", "Tipo ingreso", "Fecha comprobante", "Fecha retención", "Monto facturado", "ITBIS facturado", "ITBIS retenido por terceros", "ITBIS percibido", "Retención renta por terceros", "ISR percibido", "Impuesto selectivo al consumo", "Otros impuestos", "Propina legal", "Efectivo", "Cheque / transferencia", "Tarjeta", "Venta a crédito", "Bonos o certificados", "Permuta", "Otras formas", "Cliente"],
      ...filas.map((f) => [
        f.rnc_cedula,
        f.tipo_id,
        f.ncf,
        f.ncf_modificado,
        f.tipo_ingreso,
        fechaDgii(f.fecha_comprobante),
        fechaDgii(f.fecha_retencion),
        ...[
          f.monto_facturado,
          f.itbis_facturado,
          f.itbis_retenido_terceros,
          f.itbis_percibido,
          f.retencion_renta_terceros,
          f.isr_percibido,
          f.impuesto_selectivo_consumo,
          f.otros_impuestos,
          f.monto_propina_legal,
          f.efectivo,
          f.cheque_transferencia,
          f.tarjeta,
          f.venta_credito,
          f.bonos_certificados,
          f.permuta,
          f.otras_formas,
        ].map((v) => Number(v).toFixed(2)),
        f.cliente_nombre,
      ]),
    ]);
  }

  return (
    <>
      <Tarjetas>
        {formas.map((f) => (
          <Estadistica key={f.k} k={f.k} v={rd(f.v)} s={pct(f.v)} />
        ))}
      </Tarjetas>

      <TarjetaFormato
        titulo="Vista previa · 607 Ventas de bienes y servicios"
        sub={`${registros(totales.cantidad)} · e-CF aceptados del período`}
        validar={validar}
        generar={() => impuestosApi.descargar607(periodo)}
        exportar={filas.length > 0 ? exportar : undefined}
        mostrar={mostrar}
      >
        {datos.advertencias.length > 0 && (
          <div className="imp-pad">
            <Advertencias lista={datos.advertencias} />
          </div>
        )}
        {filas.length === 0 ? (
          <Vacio
            titulo={`Sin ventas en ${nombrePeriodo(periodo)}`}
            texto="No hay e-CF aceptados por la DGII en el período. Genere el 607 sin registros y envíelo igual desde la Oficina Virtual."
          />
        ) : (
          <>
            <div className="tw" style={{ marginTop: 12 }}>
              <table className="tbl" style={{ minWidth: 1100 }}>
                <thead>
                  <tr>
                    <th>RNC / Cédula</th>
                    <th>Tipo id.</th>
                    <th>e-NCF</th>
                    <th>NCF modificado</th>
                    <th>Tipo ingreso</th>
                    <th>Fecha</th>
                    <th className="num">Monto facturado</th>
                    <th className="num">ITBIS</th>
                    <th className="num">Efectivo</th>
                    <th className="num">Tarjeta</th>
                    <th className="num">Crédito</th>
                  </tr>
                </thead>
                <tbody>
                  {visibles.map((f, i) => (
                    <tr key={`${f.ncf}-${i}`}>
                      <td className="mono" title={f.cliente_nombre ?? undefined}>
                        {f.rnc_cedula}
                      </td>
                      <td className="mono">{f.rnc_cedula ? f.tipo_id : ""}</td>
                      <td className="mono">{f.ncf}</td>
                      <td className="mono">{f.ncf_modificado ?? ""}</td>
                      <td>{f.tipo_ingreso}</td>
                      <td>{fechaDgii(f.fecha_comprobante)}</td>
                      <td className="num">{formatoMonto(f.monto_facturado)}</td>
                      <td className="num">{formatoMonto(f.itbis_facturado)}</td>
                      <td className="num">{formatoMonto(f.efectivo)}</td>
                      <td className="num">{formatoMonto(f.tarjeta)}</td>
                      <td className="num">{formatoMonto(f.venta_credito)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={6}>Totales del período</td>
                    <td className="num">{formatoMonto(totales.monto_facturado)}</td>
                    <td className="num">{formatoMonto(totales.itbis_facturado)}</td>
                    <td className="num">{formatoMonto(formas[0].v)}</td>
                    <td className="num">{formatoMonto(formas[1].v)}</td>
                    <td className="num">{formatoMonto(formas[3].v)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
            <PieTabla
              mostrando={visibles.length}
              total={filas.length}
              archivo={nombreArchivo("607", datos.rnc_emisor, periodo)}
              nota={`ITBIS retenido por terceros RD$ ${formatoMonto(totales.itbis_retenido_terceros)} · Retención de renta RD$ ${formatoMonto(totales.retencion_renta_terceros)}`}
              onVerTodos={() => setTodos(true)}
            />
          </>
        )}
      </TarjetaFormato>
    </>
  );
}

// --- 608 · Comprobantes anulados (erp-api) ------------------------------------

const ORIGEN_608: Record<Fila608["origen"], { label: string; badge: string }> = {
  anulacion_encf: { label: "Anulación e-NCF", badge: "bd b-teal" },
  manual: { label: "Manual", badge: "bd b-borr" },
};

const NCF_VALIDO = /^(E\d{12}|B\d{10})$/;

function Formato608Vista({ periodo, declaracion, mostrar }: VistaProps) {
  const [datos, setDatos] = useState<Formato608 | null>(null);
  const [tipos, setTipos] = useState<TipoAnulacion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [enBlanco, setEnBlanco] = useState(false);
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState({ ncf: "", fecha_comprobante: "", tipo_anulacion: "" });
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [eliminar, setEliminar] = useState<Fila608 | null>(null);
  const [eliminando, setEliminando] = useState(false);

  const cargar = useCallback(async () => {
    setError(null);
    const [f, t] = await Promise.allSettled([impuestosApi.formato608(periodo), impuestosApi.tiposAnulacion()]);
    if (t.status === "fulfilled") setTipos(t.value);
    if (f.status === "fulfilled") {
      setDatos(f.value);
    } else {
      setDatos(null);
      setError(mensajeError(f.reason));
    }
  }, [periodo]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  if (error) return <AvisoError titulo="No se pudo cargar el formato 608." mensaje={error} onReintentar={() => void cargar()} />;
  if (!datos) return <Cargando />;

  const filas = datos.filas;
  const sinFilas = filas.length === 0;
  const blanco = sinFilas && enBlanco;

  function abrirForm() {
    setForm({ ncf: "", fecha_comprobante: "", tipo_anulacion: tipos[0]?.codigo ?? "" });
    setErrorForm(null);
    setAbierto(true);
  }

  async function agregar() {
    const ncf = form.ncf.trim().toUpperCase();
    if (!NCF_VALIDO.test(ncf)) return setErrorForm("Escriba un e-NCF de 13 caracteres (E310000000123) o un NCF de 11 (B0100000123).");
    if (!form.fecha_comprobante) return setErrorForm("Indique la fecha del comprobante anulado.");
    if (!form.tipo_anulacion) return setErrorForm("Elija el motivo de anulación.");
    setGuardando(true);
    setErrorForm(null);
    try {
      const fila = await impuestosApi.agregarAnulacion608({ ncf, fecha_comprobante: form.fecha_comprobante, tipo_anulacion: form.tipo_anulacion });
      setAbierto(false);
      setEnBlanco(false);
      setVersion((v) => v + 1);
      mostrar("Anulación agregada", { detalle: `${fila.ncf} · ${fila.tipo_anulacion} ${fila.tipo_anulacion_label}` });
      void cargar();
    } catch (err) {
      setErrorForm(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarEliminar() {
    if (!eliminar?.id) return;
    setEliminando(true);
    try {
      await impuestosApi.eliminarAnulacion608(eliminar.id);
      mostrar("Anulación eliminada", { detalle: eliminar.ncf });
      setVersion((v) => v + 1);
      void cargar();
    } catch (err) {
      mostrar("No se pudo eliminar la anulación", { detalle: mensajeError(err), error: true });
    } finally {
      setEliminar(null);
      setEliminando(false);
    }
  }

  async function validar(): Promise<Validacion> {
    const d = await impuestosApi.formato608(periodo);
    setDatos(d);
    const n = d.filas.length;
    return {
      errores: [],
      ok:
        n === 0
          ? "Reporte 608 en blanco listo para generar."
          : `${n} ${n === 1 ? "comprobante anulado" : "comprobantes anulados"} con motivo asignado.`,
    };
  }

  function exportar() {
    descargarCsv(`Formato_608_${periodo}.csv`, [
      ["e-NCF", "Fecha comprobante", "Tipo de anulación", "Motivo", "Origen"],
      ...filas.map((f) => [f.ncf, fechaDgii(f.fecha_comprobante), f.tipo_anulacion, f.tipo_anulacion_label, ORIGEN_608[f.origen]?.label ?? f.origen]),
    ]);
  }

  return (
    <>
      <Tarjetas>
        <Estadistica k="Comprobantes anulados" v={String(filas.length)} s={nombrePeriodo(periodo, true)} />
        <Estadistica k="Crédito fiscal (E31)" v={String(filas.filter((f) => /^(E31|B01)/.test(f.ncf)).length)} s="Facturas de crédito fiscal" />
        <Estadistica k="Consumo (E32)" v={String(filas.filter((f) => /^(E32|B02)/.test(f.ncf)).length)} s="Facturas de consumo" />
        <TarjetaVence declaracion={declaracion} />
      </Tarjetas>

      <div className="card imp-blanco">
        <button
          type="button"
          className={blanco ? "sw on" : "sw"}
          onClick={() => setEnBlanco((v) => !v)}
          disabled={!sinFilas}
          aria-pressed={blanco}
          aria-label="No hubo anulaciones en el período"
        />
        <div>
          <div className="strong">No hubo anulaciones en el período</div>
          <div className="hint">
            {sinFilas
              ? "El 608 debe presentarse igual: se generará un reporte en blanco (sin registros)."
              : `Hay ${filas.length} ${filas.length === 1 ? "comprobante anulado" : "comprobantes anulados"} en el período; el reporte se generará con ${filas.length === 1 ? "él" : "ellos"}.`}
          </div>
        </div>
      </div>

      <TarjetaFormato
        titulo="Vista previa · 608 Comprobantes anulados"
        sub={sinFilas ? "Declaración en cero si no se anuló ningún comprobante" : "Motivo de anulación de cada comprobante"}
        acciones={
          <button type="button" className="btn btn-o btn-sm" onClick={() => (abierto ? setAbierto(false) : abrirForm())}>
            {abierto ? "Cerrar formulario" : "+ Agregar anulación"}
          </button>
        }
        validar={validar}
        generar={() => impuestosApi.descargar608(periodo)}
        exportar={sinFilas ? undefined : exportar}
        genLabel={sinFilas ? "Generar reporte en blanco" : "Generar archivo TXT"}
        bloqueo={sinFilas && !blanco ? "Confirme arriba que no hubo anulaciones para generar el reporte en blanco." : null}
        version={version}
        mostrar={mostrar}
      >
        {abierto && (
          <div className="card-b form-grid imp-form">
            <div className="fld">
              <label className="lbl" htmlFor="an-ncf">
                e-NCF o NCF anulado <span className="req">*</span>
              </label>
              <input
                id="an-ncf"
                className="inp mono"
                value={form.ncf}
                maxLength={13}
                onChange={(e) => setForm((f) => ({ ...f, ncf: e.target.value.toUpperCase() }))}
                placeholder="E310000000123"
              />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="an-fecha">
                Fecha del comprobante <span className="req">*</span>
              </label>
              <input id="an-fecha" type="date" className="inp" value={form.fecha_comprobante} onChange={(e) => setForm((f) => ({ ...f, fecha_comprobante: e.target.value }))} />
            </div>
            <div className="fld" style={{ gridColumn: "span 2" }}>
              <label className="lbl" htmlFor="an-tipo">
                Motivo de anulación <span className="req">*</span>
              </label>
              <select id="an-tipo" className="inp" value={form.tipo_anulacion} onChange={(e) => setForm((f) => ({ ...f, tipo_anulacion: e.target.value }))}>
                {tipos.length === 0 && <option value="">Catálogo no disponible</option>}
                {tipos.map((t) => (
                  <option key={t.codigo} value={t.codigo}>
                    {t.codigo} · {t.descripcion}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld-full hint">
              Los e-CF anulados con la DGII (ANECF) ya aparecen solos. Agregue aquí los comprobantes en papel o los que anuló por otra vía.
            </div>
            {errorForm && (
              <div className="al al-d fld-full" role="alert">
                <IconAlerta />
                <span>
                  <b>{errorForm}</b>
                </span>
              </div>
            )}
            <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-o" onClick={() => setAbierto(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={agregar} disabled={guardando}>
                {guardando ? "Agregando…" : "Agregar anulación"}
              </button>
            </div>
          </div>
        )}
        {datos.advertencias.length > 0 && (
          <div className="imp-pad">
            <Advertencias lista={datos.advertencias} />
          </div>
        )}
        {sinFilas ? (
          <div className="empty">
            <span className="ei">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
                <path d="M14 3v6h6M9 15l2 2 4-4" />
              </svg>
            </span>
            <h3>Sin anulaciones en {nombrePeriodo(periodo)}</h3>
            <p>
              No se anuló ningún comprobante en el período. Genere el 608 en blanco (declaración en cero) y envíelo desde la Oficina Virtual para cumplir con la
              obligación.
            </p>
          </div>
        ) : (
          <>
            <div className="tw" style={{ marginTop: 12 }}>
              <table className="tbl" style={{ minWidth: 760 }}>
                <thead>
                  <tr>
                    <th>e-NCF</th>
                    <th>Fecha</th>
                    <th>Origen</th>
                    <th>Motivo de anulación</th>
                    <th style={{ textAlign: "right" }}>Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map((f, i) => {
                    const o = ORIGEN_608[f.origen] ?? { label: f.origen, badge: "bd b-borr" };
                    return (
                      <tr key={f.id ?? `${f.ncf}-${i}`}>
                        <td className="mono">{f.ncf}</td>
                        <td>{fechaDgii(f.fecha_comprobante)}</td>
                        <td>
                          <span className={o.badge}>{o.label}</span>
                        </td>
                        <td>
                          <span className="mono mut">{f.tipo_anulacion}</span> · {f.tipo_anulacion_label}
                        </td>
                        <td>
                          <div className="fila-acc">
                            {f.origen === "manual" && f.id && (
                              <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setEliminar(f)}>
                                Eliminar
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="imp-pie">
              Archivo: <span className="mono">{nombreArchivo("608", datos.rnc_emisor, periodo)}</span>
            </div>
          </>
        )}
      </TarjetaFormato>

      {eliminar && (
        <Modal
          titulo="¿Eliminar esta anulación?"
          onCerrar={() => setEliminar(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEliminar(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarEliminar} disabled={eliminando}>
                {eliminando ? "Eliminando…" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          <span>
            El comprobante{" "}
            <b className="mono" style={{ color: "#111827" }}>
              {eliminar.ncf}
            </b>{" "}
            dejará de figurar en el 608 de {nombrePeriodo(periodo)}.
          </span>
        </Modal>
      )}
    </>
  );
}
