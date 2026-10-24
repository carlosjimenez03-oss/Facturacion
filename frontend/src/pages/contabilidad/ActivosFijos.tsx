// Pestaña Activos fijos de Contabilidad.dc.html: lista con costo, tasa DGII, depreciación acumulada y valor en
// libros; "Calcular depreciación de <mes>" (asiento del mes), alta y edición de activos, el cuadro de
// depreciación de cada uno y la depreciación fiscal del año que se sugiere en el IR-2.
import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type {
  ActivoFijo,
  ActivoFijoPayload,
  CategoriaDgii,
  CuentaContable,
  DepreciacionFiscal,
  DepreciacionPeriodo,
  MetodoDepreciacion,
  TablaDepreciacion,
} from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { nombrePeriodo, periodosRecientes } from "../impuestos/impuestosUtil";
import type { MostrarAviso } from "./Contabilidad";
import { CATEGORIAS_DGII, dec2, etiquetaCuentaContable, METODO_DEPRECIACION, usePeriodoContable } from "./contabilidadUtil";

const ESTADO_ACTIVO: Record<ActivoFijo["estado"], { label: string; badge: string }> = {
  activo: { label: "En uso", badge: "bd b-acep" },
  depreciado: { label: "Depreciado", badge: "bd b-teal" },
  baja: { label: "De baja", badge: "bd b-borr" },
};

/** AAAAMM <-> valor de un <input type="month"> (AAAA-MM). */
const aMes = (p: string | null | undefined) => (p && /^\d{6}$/.test(p) ? `${p.slice(0, 4)}-${p.slice(4, 6)}` : "");
const dePeriodo = (m: string) => m.replace("-", "");

export function ActivosTab({ mostrarAviso }: { mostrarAviso: MostrarAviso }) {
  const [periodo, setPeriodo] = usePeriodoContable();
  const [activos, setActivos] = useState<ActivoFijo[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dep, setDep] = useState<DepreciacionPeriodo | null>(null);
  const [errorDep, setErrorDep] = useState<string | null>(null);
  const [fiscal, setFiscal] = useState<DepreciacionFiscal | null>(null);
  const [confirmar, setConfirmar] = useState(false);
  const [registrando, setRegistrando] = useState(false);
  const [errorRegistro, setErrorRegistro] = useState<string | null>(null);
  const [form, setForm] = useState<{ activo: ActivoFijo | null } | null>(null);
  const [cuadro, setCuadro] = useState<ActivoFijo | null>(null);
  const formRef = useRef<HTMLElement>(null);
  const anio = periodo.slice(0, 4);

  const cargar = useCallback(async () => {
    setCargando(true);
    const [lista, d, f] = await Promise.allSettled([
      contabilidadApi.listarActivos(),
      contabilidadApi.depreciacionDelPeriodo(periodo),
      contabilidadApi.depreciacionFiscal(periodo.slice(0, 4)),
    ]);
    if (lista.status === "fulfilled") {
      setActivos(lista.value);
      setError(null);
    } else {
      setActivos([]);
      setError(mensajeError(lista.reason));
    }
    if (d.status === "fulfilled") {
      setDep(d.value);
      setErrorDep(null);
    } else {
      setDep(null);
      setErrorDep(mensajeError(d.reason));
    }
    setFiscal(f.status === "fulfilled" ? f.value : null);
    setCargando(false);
  }, [periodo]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  async function registrar() {
    setRegistrando(true);
    setErrorRegistro(null);
    try {
      const r = await contabilidadApi.registrarDepreciacion(periodo);
      setConfirmar(false);
      mostrarAviso(`Depreciación de ${nombrePeriodo(periodo).split(" ")[0]} registrada en el asiento ${r.asiento.numero}`, { detalle: `RD$ ${formatoMonto(r.total)} · ${r.activos.length} activos` });
      void cargar();
    } catch (err) {
      setErrorRegistro(mensajeError(err));
    } finally {
      setRegistrando(false);
    }
  }

  function abrirForm(activo: ActivoFijo | null) {
    setForm({ activo });
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  const mes = nombrePeriodo(periodo).split(" ")[0];
  const hayCuota = dep ? Number(dep.total) > 0 : false;

  return (
    <>
      <section className="card card-b ct-ctl">
        <div className="fld">
          <label className="lbl" htmlFor="af-per">Mes a depreciar</label>
          <select id="af-per" className="inp" value={periodo} onChange={(e) => setPeriodo(e.target.value)}>
            {periodosRecientes(24).map((p) => (
              <option key={p} value={p}>
                {nombrePeriodo(p, true)}
              </option>
            ))}
          </select>
        </div>
        <span className="hint grow">La depreciación se registra con un asiento el último día del mes (gasto de depreciación contra la depreciación acumulada de cada categoría).</span>
        <button type="button" className="btn btn-p" onClick={() => abrirForm(null)}>
          + Nuevo activo
        </button>
      </section>

      {form && (
        <ActivoForm
          key={form.activo?.id ?? "nuevo"}
          refSeccion={formRef}
          activo={form.activo}
          onCancelar={() => setForm(null)}
          onGuardado={(a, nuevo) => {
            setForm(null);
            mostrarAviso(nuevo ? "Activo fijo registrado" : "Activo actualizado", { detalle: `${a.descripcion} · cuota mensual RD$ ${formatoMonto(a.cuota_mensual)}` });
            void cargar();
          }}
          onEliminado={(a) => {
            setForm(null);
            mostrarAviso("Activo eliminado", { detalle: a.descripcion });
            void cargar();
          }}
        />
      )}

      {error && <AvisoError titulo="No se pudieron cargar los activos fijos." mensaje={error} onReintentar={() => void cargar()} />}
      {dep && dep.advertencias.length > 0 && (
        <div className="al al-w" role="status">
          <IconAlerta />
          <div>
            {dep.advertencias.length === 1 ? (
              <span>{dep.advertencias[0]}</span>
            ) : (
              <>
                <b>Revise antes de depreciar:</b>
                <ul className="ct-lista">
                  {dep.advertencias.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        </div>
      )}

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Activos fijos</h2>
            {dep && (
              <span className="hint">
                Depreciación de {dep.nombre_periodo}: RD$ {formatoMonto(dep.total)}
                {dep.registrada && dep.asiento_numero ? ` · asiento ${dep.asiento_numero}` : ""}
              </span>
            )}
            {errorDep && !error && <span className="hint err-t">No se pudo calcular la depreciación del mes: {errorDep}</span>}
          </div>
          <button
            type="button"
            className="btn btn-p btn-sm"
            onClick={() => {
              setErrorRegistro(null);
              setConfirmar(true);
            }}
            disabled={!dep || dep.registrada || !hayCuota}
          >
            {dep?.registrada ? `Depreciación de ${mes} registrada` : `Calcular depreciación de ${mes}`}
          </button>
        </div>
        {cargando && activos.length === 0 && <Cargando />}
        {!cargando && !error && activos.length === 0 && (
          <Vacio titulo="No hay activos fijos registrados" texto="Registre los vehículos, equipos, mobiliario y edificaciones de la empresa para calcular su depreciación mensual y la fiscal del IR-2.">
            <button type="button" className="btn btn-p btn-sm" onClick={() => abrirForm(null)}>
              + Nuevo activo
            </button>
          </Vacio>
        )}
        {activos.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 980 }}>
              <thead>
                <tr>
                  <th>Activo</th>
                  <th>Categoría DGII</th>
                  <th className="num">Costo</th>
                  <th className="num">Tasa</th>
                  <th className="num">Dep. acumulada</th>
                  <th className="num">Valor en libros</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {activos.map((a) => {
                  const est = ESTADO_ACTIVO[a.estado] ?? { label: a.estado, badge: "bd b-borr" };
                  return (
                    <tr key={a.id}>
                      <td style={{ fontWeight: 500 }}>
                        {a.descripcion}
                        <div className="hint">
                          Adquirido el {fechaLegible(a.fecha_adquisicion)} · {METODO_DEPRECIACION[a.metodo]}
                          {a.fecha_baja ? ` · baja ${fechaLegible(a.fecha_baja)}${a.valor_venta ? `, vendido por RD$ ${formatoMonto(a.valor_venta)}` : ""}` : ""}
                          {a.numero_serie ? ` · ${a.numero_serie}` : ""}
                        </div>
                      </td>
                      <td className="mut">
                        Categoría {a.categoria} · {Number(a.tasa_dgii)}%
                      </td>
                      <td className="num">{formatoMonto(a.costo)}</td>
                      <td className="num">{Number(a.tasa_dgii)}%</td>
                      <td className="num">{formatoMonto(a.depreciacion_acumulada)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {formatoMonto(a.valor_en_libros)}
                      </td>
                      <td>
                        <span className={est.badge}>{est.label}</span>
                      </td>
                      <td>
                        <div className="fila-acc">
                          <button type="button" className="btn btn-g btn-sm" onClick={() => setCuadro(a)}>
                            Cuadro
                          </button>
                          <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(a)}>
                            Editar
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {fiscal && (
        <section className="card">
          <div className="card-b ct-fiscal">
            <div className="grow">
              <div className="k" style={{ fontSize: 13, color: "#6B7280", fontWeight: 500 }}>
                Depreciación fiscal {fiscal.anio} (Art. 287 del Código Tributario)
              </div>
              <div className="v">RD$ {formatoMonto(fiscal.total)}</div>
              <span className="hint">
                Categoría 1 al 5 % del costo, activo por activo; categorías 2 y 3 sobre la cuenta de cada categoría (valor al inicio + adiciones − ventas). Es el valor sugerido del ajuste
                «Depreciación» del IR-2.
              </span>
            </div>
            <Link className="btn btn-o" to={`/impuestos/precalculo/ir2?anio=${anio}`}>
              Ver en el pre-cálculo IR-2 {anio}
            </Link>
          </div>
          {Number(fiscal.ingreso_gravable ?? 0) > 0 && (
            <div className="card-b" style={{ paddingTop: 0 }}>
              <div className="al al-w" role="status">
                <IconAlerta />
                <span>
                  Las ventas de activos superan la cuenta de su categoría: RD$ {formatoMonto(fiscal.ingreso_gravable)} son renta gravable del {fiscal.anio} en el IR-2.
                </span>
              </div>
            </div>
          )}
          {(fiscal.categorias ?? []).length > 0 && (
            <div className="tw">
              <table className="tbl" style={{ minWidth: 820 }}>
                <thead>
                  <tr>
                    <th>Cuenta de la categoría</th>
                    <th className="num">Valor al inicio</th>
                    <th className="num">Adiciones</th>
                    <th className="num">Ventas</th>
                    <th className="num">Base</th>
                    <th className="num">Depreciación</th>
                    <th className="num">Valor al cierre</th>
                  </tr>
                </thead>
                <tbody>
                  {fiscal.categorias.map((c) => (
                    <tr key={c.categoria}>
                      <td>
                        Categoría {c.categoria} · {Number(c.tasa_dgii)}%<div className="hint">{c.nombre}</div>
                      </td>
                      <td className="num">{formatoMonto(c.valor_inicio)}</td>
                      <td className="num">{formatoMonto(c.adiciones)}</td>
                      <td className="num">{formatoMonto(c.retiros)}</td>
                      <td className="num">{formatoMonto(c.base)}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {formatoMonto(c.depreciacion)}
                      </td>
                      <td className="num">{formatoMonto(c.valor_final)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {confirmar && dep && (
        <Modal
          titulo={`Calcular depreciación de ${dep.nombre_periodo}`}
          onCerrar={() => setConfirmar(false)}
          ancho={640}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setConfirmar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-p" onClick={registrar} disabled={registrando}>
                {registrando ? "Registrando…" : `Registrar RD$ ${formatoMonto(dep.total)}`}
              </button>
            </>
          }
        >
          <span>Se registra un asiento con la cuota del mes de cada activo. Si después cambia algo, anule el asiento y vuelva a calcularla.</span>
          <div className="tw" style={{ border: "1px solid #E5E7EB", borderRadius: 10 }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Activo</th>
                  <th>Cat.</th>
                  <th className="num">Cuota (RD$)</th>
                </tr>
              </thead>
              <tbody>
                {dep.activos.map((a) => (
                  <tr key={a.activo_id}>
                    <td>
                      {a.descripcion}
                      {a.meses_sin_registrar_antes > 0 && <div className="hint err-t">{a.meses_sin_registrar_antes} meses anteriores sin registrar</div>}
                    </td>
                    <td className="mut">{a.categoria}</td>
                    <td className="num">{formatoMonto(a.cuota)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}>Total</td>
                  <td className="num">{formatoMonto(dep.total)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          {errorRegistro && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>
                <b>{errorRegistro}</b>
              </span>
            </div>
          )}
        </Modal>
      )}

      {cuadro && <CuadroDepreciacion activo={cuadro} onCerrar={() => setCuadro(null)} />}
    </>
  );
}

interface CamposActivo {
  descripcion: string;
  categoria: CategoriaDgii;
  fecha_adquisicion: string;
  costo: string;
  valor_residual: string;
  metodo: MetodoDepreciacion;
  vida_util_meses: string;
  depreciar_desde: string;
  depreciacion_acumulada_inicial: string;
  cuenta_activo_id: string;
  numero_serie: string;
  ubicacion: string;
  notas: string;
}

function camposDe(a: ActivoFijo | null): CamposActivo {
  const num = (v: string | null | undefined) => (v && Number(v) ? String(Number(v)) : "");
  return {
    descripcion: a?.descripcion ?? "",
    categoria: a?.categoria ?? 2,
    fecha_adquisicion: a?.fecha_adquisicion ?? hoyIso(),
    costo: num(a?.costo),
    valor_residual: num(a?.valor_residual),
    metodo: a?.metodo ?? "dgii",
    vida_util_meses: a?.vida_util_meses ? String(a.vida_util_meses) : "",
    depreciar_desde: aMes(a?.depreciar_desde),
    depreciacion_acumulada_inicial: num(a?.depreciacion_acumulada_inicial),
    cuenta_activo_id: a?.cuenta_activo_id ?? "",
    numero_serie: a?.numero_serie ?? "",
    ubicacion: a?.ubicacion ?? "",
    notas: a?.notas ?? "",
  };
}

function ActivoForm({
  refSeccion,
  activo,
  onCancelar,
  onGuardado,
  onEliminado,
}: {
  refSeccion: RefObject<HTMLElement>;
  activo: ActivoFijo | null;
  onCancelar: () => void;
  onGuardado: (a: ActivoFijo, nuevo: boolean) => void;
  onEliminado: (a: ActivoFijo) => void;
}) {
  const [f, setF] = useState<CamposActivo>(() => camposDe(activo));
  const [cuentas, setCuentas] = useState<CuentaContable[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [baja, setBaja] = useState(activo?.fecha_baja ?? "");
  const [valorVenta, setValorVenta] = useState(activo?.valor_venta && Number(activo.valor_venta) ? String(Number(activo.valor_venta)) : "");
  const conDepreciacion = !!activo && Number(activo.depreciacion_registrada) > 0;

  useEffect(() => {
    contabilidadApi
      .listarCuentas({ solo_movimiento: true, activa: true })
      .then((l) => setCuentas(l.filter((c) => c.tipo === "activo" && c.codigo.startsWith("1.2"))))
      .catch(() => setCuentas([]));
  }, []);

  function set<K extends keyof CamposActivo>(campo: K, valor: CamposActivo[K]) {
    setF((prev) => ({ ...prev, [campo]: valor }));
    setError(null);
  }

  function payload(): ActivoFijoPayload {
    return {
      descripcion: f.descripcion.trim(),
      categoria: f.categoria,
      fecha_adquisicion: f.fecha_adquisicion,
      costo: dec2(f.costo),
      valor_residual: dec2(f.valor_residual),
      metodo: f.metodo,
      vida_util_meses: f.metodo === "linea_recta" ? Number(f.vida_util_meses) : null,
      depreciar_desde: f.depreciar_desde ? dePeriodo(f.depreciar_desde) : null,
      depreciacion_acumulada_inicial: dec2(f.depreciacion_acumulada_inicial),
      cuenta_activo_id: f.cuenta_activo_id || null,
      numero_serie: f.numero_serie.trim() || null,
      ubicacion: f.ubicacion.trim() || null,
      notas: f.notas.trim() || null,
    };
  }

  async function guardar() {
    if (!f.descripcion.trim()) return setError("Describa el activo (p. ej. «Camión Isuzu NPR 2024»).");
    if (!(Number(f.costo) > 0)) return setError("El costo debe ser mayor que cero.");
    if (Number(f.valor_residual) >= Number(f.costo)) return setError("El valor residual debe ser menor que el costo.");
    if (f.metodo === "linea_recta" && !(Number(f.vida_util_meses) > 0)) return setError("Indique la vida útil en meses para la línea recta.");
    if (!f.fecha_adquisicion) return setError("Indique la fecha de adquisición.");
    setGuardando(true);
    try {
      const p = payload();
      if (!activo) {
        onGuardado(await contabilidadApi.crearActivo(p), true);
      } else {
        // Solo lo que cambió: con depreciación registrada, costo/categoría/método/fechas dan 409.
        const antes = camposDe(activo);
        const cambios: Partial<ActivoFijoPayload> = {};
        (Object.keys(f) as (keyof CamposActivo)[]).forEach((k) => {
          if (f[k] !== antes[k]) Object.assign(cambios, { [k]: p[k as keyof ActivoFijoPayload] });
        });
        onGuardado(Object.keys(cambios).length ? await contabilidadApi.actualizarActivo(activo.id, cambios) : activo, false);
      }
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  async function aplicarBaja(fecha: string | null) {
    if (!activo) return;
    try {
      onGuardado(await contabilidadApi.darBajaActivo(activo.id, fecha, fecha && valorVenta.trim() ? dec2(valorVenta) : null), false);
    } catch (err) {
      setError(mensajeError(err));
    }
  }

  async function eliminar() {
    if (!activo) return;
    try {
      await contabilidadApi.eliminarActivo(activo.id);
      onEliminado(activo);
    } catch (err) {
      setError(mensajeError(err));
    }
  }

  const categoria = CATEGORIAS_DGII.find((c) => c.value === f.categoria)!;
  const bloq = conDepreciacion;

  return (
    <section className="card" ref={refSeccion}>
      <div className="card-h">
        <h2>{activo ? `Editar · ${activo.descripcion}` : "Nuevo activo fijo"}</h2>
        <span className="hint">Tasa DGII {categoria.tasa}% anual</span>
      </div>
      <div className="card-b form-grid">
        {bloq && (
          <div className="al al-i fld-full">
            <IconAlerta />
            <span>Ya tiene depreciación registrada: el costo, la categoría, el método y las fechas no se cambian (anule los asientos de depreciación o dé de baja el activo).</span>
          </div>
        )}
        <div className="fld" style={{ gridColumn: "span 2" }}>
          <label className="lbl" htmlFor="af-desc">
            Descripción <span className="req">*</span>
          </label>
          <input id="af-desc" className="inp" value={f.descripcion} onChange={(e) => set("descripcion", e.target.value)} placeholder="Camión Isuzu NPR 2024" />
        </div>
        <div className="fld" style={{ gridColumn: "span 2" }}>
          <label className="lbl" htmlFor="af-cat">Categoría DGII</label>
          <select id="af-cat" className="inp" value={f.categoria} onChange={(e) => set("categoria", Number(e.target.value) as CategoriaDgii)} disabled={bloq}>
            {CATEGORIAS_DGII.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-fecha">Fecha de adquisición</label>
          <input id="af-fecha" type="date" className="inp" value={f.fecha_adquisicion} max={hoyIso()} onChange={(e) => set("fecha_adquisicion", e.target.value)} disabled={bloq} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-costo">
            Costo (RD$) <span className="req">*</span>
          </label>
          <input id="af-costo" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={f.costo} onChange={(e) => set("costo", e.target.value)} disabled={bloq} placeholder="0.00" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-res">Valor residual (RD$)</label>
          <input id="af-res" type="number" min="0" step="0.01" className="inp" style={{ textAlign: "right" }} value={f.valor_residual} onChange={(e) => set("valor_residual", e.target.value)} disabled={bloq} placeholder="0.00" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-met">Método de los asientos</label>
          <select id="af-met" className="inp" value={f.metodo} onChange={(e) => set("metodo", e.target.value as MetodoDepreciacion)} disabled={bloq}>
            <option value="dgii">{METODO_DEPRECIACION.dgii}</option>
            <option value="linea_recta">{METODO_DEPRECIACION.linea_recta}</option>
          </select>
          <span className="hint">El IR-2 usa siempre el método fiscal.</span>
        </div>
        {f.metodo === "linea_recta" && (
          <div className="fld">
            <label className="lbl" htmlFor="af-vida">
              Vida útil (meses) <span className="req">*</span>
            </label>
            <input id="af-vida" type="number" min="1" step="1" className="inp" value={f.vida_util_meses} onChange={(e) => set("vida_util_meses", e.target.value)} disabled={bloq} placeholder="60" />
          </div>
        )}
        <div className="fld">
          <label className="lbl" htmlFor="af-desde">Depreciar desde</label>
          <input id="af-desde" type="month" className="inp" value={f.depreciar_desde} onChange={(e) => set("depreciar_desde", e.target.value)} disabled={bloq} />
          <span className="hint">Vacío: el mes siguiente a la adquisición.</span>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-acum">Depreciación acumulada previa (RD$)</label>
          <input
            id="af-acum"
            type="number"
            min="0"
            step="0.01"
            className="inp"
            style={{ textAlign: "right" }}
            value={f.depreciacion_acumulada_inicial}
            onChange={(e) => set("depreciacion_acumulada_inicial", e.target.value)}
            disabled={bloq}
            placeholder="0.00"
          />
          <span className="hint">Si el activo ya venía depreciándose.</span>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-cta">Cuenta del activo</label>
          <select id="af-cta" className="inp" value={f.cuenta_activo_id} onChange={(e) => set("cuenta_activo_id", e.target.value)}>
            <option value="">Según la categoría (por defecto)</option>
            {cuentas.map((c) => (
              <option key={c.id} value={c.id}>
                {etiquetaCuentaContable(c)}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-serie">N.º de serie / placa</label>
          <input id="af-serie" className="inp mono" value={f.numero_serie} onChange={(e) => set("numero_serie", e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="af-ubi">Ubicación</label>
          <input id="af-ubi" className="inp" value={f.ubicacion} onChange={(e) => set("ubicacion", e.target.value)} placeholder="Almacén principal" />
        </div>
        <div className="fld fld-full">
          <label className="lbl" htmlFor="af-not">Notas</label>
          <input id="af-not" className="inp" value={f.notas} onChange={(e) => set("notas", e.target.value)} />
        </div>
        {activo && (
          <div className="fld">
            <label className="lbl" htmlFor="af-baja">Fecha de baja</label>
            <div className="row" style={{ flexWrap: "nowrap" }}>
              <input id="af-baja" type="date" className="inp" value={baja} max={hoyIso()} onChange={(e) => setBaja(e.target.value)} />
              {activo.fecha_baja ? (
                <button type="button" className="btn btn-o" onClick={() => void aplicarBaja(null)}>
                  Revertir
                </button>
              ) : (
                <button type="button" className="btn btn-o" onClick={() => baja && void aplicarBaja(baja)} disabled={!baja}>
                  Dar de baja
                </button>
              )}
            </div>
            <span className="hint">Deja de depreciarse desde el mes siguiente.</span>
          </div>
        )}
        {activo && (
          <div className="fld">
            <label className="lbl" htmlFor="af-venta">Valor de venta (RD$)</label>
            <input
              id="af-venta"
              type="number"
              min="0"
              step="0.01"
              className="inp"
              style={{ textAlign: "right" }}
              value={valorVenta}
              onChange={(e) => setValorVenta(e.target.value)}
              disabled={!!activo.fecha_baja}
              placeholder="Vacío si se retiró sin venderse"
            />
            <span className="hint">Rebaja la cuenta fiscal de su categoría en el IR-2.</span>
          </div>
        )}
        {error && (
          <div className="al al-d fld-full" role="alert">
            <IconAlerta />
            <span>
              <b>{error}</b>
            </span>
          </div>
        )}
        <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
          {activo && !conDepreciacion && (
            <button type="button" className="btn btn-g" style={{ color: "#B91C1C", marginRight: "auto" }} onClick={() => void eliminar()}>
              Eliminar activo
            </button>
          )}
          <button type="button" className="btn btn-o" onClick={onCancelar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando}>
            {guardando ? "Guardando…" : "Guardar activo"}
          </button>
        </div>
      </div>
    </section>
  );
}

function CuadroDepreciacion({ activo, onCerrar }: { activo: ActivoFijo; onCerrar: () => void }) {
  const [tabla, setTabla] = useState<TablaDepreciacion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasta, setHasta] = useState(`${new Date().getFullYear()}-12`);

  const cargar = useCallback(() => {
    setError(null);
    contabilidadApi
      .tablaDepreciacion(activo.id, dePeriodo(hasta))
      .then(setTabla)
      .catch((err) => setError(mensajeError(err)));
  }, [activo.id, hasta]);

  useEffect(cargar, [cargar]);

  return (
    <Modal
      titulo={`Cuadro de depreciación · ${activo.descripcion}`}
      onCerrar={onCerrar}
      ancho={760}
      pie={
        <button type="button" className="btn btn-p" onClick={onCerrar}>
          Cerrar
        </button>
      }
    >
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
        <span>
          Costo RD$ {formatoMonto(activo.costo)} · {METODO_DEPRECIACION[activo.metodo]} · categoría {activo.categoria} ({Number(activo.tasa_dgii)}%)
        </span>
        <div className="fld">
          <label className="lbl" htmlFor="cd-hasta">Hasta</label>
          <input id="cd-hasta" type="month" className="inp" value={hasta} onChange={(e) => e.target.value && setHasta(e.target.value)} />
        </div>
      </div>
      {error && <AvisoError titulo="No se pudo calcular el cuadro." mensaje={error} onReintentar={cargar} />}
      {!tabla && !error && <Cargando />}
      {tabla && (
        <>
          <div className="tw" style={{ border: "1px solid #E5E7EB", borderRadius: 10, maxHeight: 360, overflowY: "auto" }}>
            <table className="tbl" style={{ minWidth: 600 }}>
              <thead>
                <tr>
                  <th>Mes</th>
                  <th className="num">Cuota</th>
                  <th className="num">Acumulada</th>
                  <th className="num">Valor en libros</th>
                  <th>Asiento</th>
                </tr>
              </thead>
              <tbody>
                {tabla.cuotas.map((c) => (
                  <tr key={c.periodo}>
                    <td className="nowrap">{nombrePeriodo(c.periodo, true)}</td>
                    <td className="num">{formatoMonto(c.registrada && c.monto_registrado ? c.monto_registrado : c.cuota)}</td>
                    <td className="num">{formatoMonto(c.acumulada)}</td>
                    <td className="num">{formatoMonto(c.valor_en_libros)}</td>
                    <td>{c.registrada ? <span className="bd b-acep">{c.asiento_numero ?? "Registrada"}</span> : <span className="mut">Pendiente</span>}</td>
                  </tr>
                ))}
                {tabla.cuotas.length === 0 && (
                  <tr>
                    <td colSpan={5} className="mut" style={{ textAlign: "center" }}>
                      Sin cuotas hasta ese mes.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {tabla.fiscal.length > 0 && (
            <dl className="kv" style={{ margin: 0 }}>
              {tabla.fiscal.map((x) => (
                <FilaFiscal key={x.anio} anio={x.anio} monto={x.depreciacion} />
              ))}
            </dl>
          )}
        </>
      )}
    </Modal>
  );
}

function FilaFiscal({ anio, monto }: { anio: string; monto: string }) {
  return (
    <>
      <dt>Su parte de la depreciación fiscal {anio}</dt>
      <dd>
        RD$ {formatoMonto(monto)} ·{" "}
        <Link className="link" to={`/impuestos/precalculo/ir2?anio=${anio}`}>
          IR-2 {anio}
        </Link>
      </dd>
    </>
  );
}
