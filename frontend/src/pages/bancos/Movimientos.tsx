// Pestaña Movimientos de Bancos.dc.html: movimientos de la cuenta elegida (registrados en Bancos, cobros y
// pagos a proveedores) con el balance después de cada uno, filtros, exportación del extracto y el registro
// de depósitos, retiros, cargos, intereses y comisiones.
import { useCallback, useEffect, useState } from "react";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { MovimientoBancario, MovimientosCuenta, TipoMovimientoManual } from "../../lib/bancosApi";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { CuentaContable } from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Buscador, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { descargarCsv, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { nombreArchivo } from "../inventario/inventarioUtil";
import type { PestanaBancosProps } from "./Bancos";
import { montoMoneda, simboloMoneda, TIPO_MOVIMIENTO_LABEL, TIPOS_MOVIMIENTO_MANUAL } from "./bancosUtil";

interface FormMovimiento {
  tipo: TipoMovimientoManual;
  fecha: string;
  monto: string;
  descripcion: string;
  referencia: string;
  cuenta_contable_id: string;
  tasa_cambio: string;
}

function formVacio(): FormMovimiento {
  return { tipo: "deposito", fecha: hoyIso(), monto: "", descripcion: "", referencia: "", cuenta_contable_id: "", tasa_cambio: "" };
}

/** Contrapartida que usan los asientos si no se elige otra. */
const CONTRAPARTIDA: Record<TipoMovimientoManual, string> = {
  deposito: "Caja general",
  retiro: "Caja general",
  cargo_bancario: "Gastos bancarios",
  comision: "Gastos bancarios",
  interes: "Ingresos financieros",
};

function sePuedeAnular(m: MovimientoBancario): boolean {
  return m.origen === "manual" && !m.transferencia_id && !m.anulado;
}

export function MovimientosTab({ cuenta, recargarCuentas, mostrarAviso, onEditarCuenta }: PestanaBancosProps) {
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [estado, setEstado] = useState<"" | "si" | "no">("");
  const [q, setQ] = useState("");
  const [qAplicada, setQAplicada] = useState("");
  const [datos, setDatos] = useState<MovimientosCuenta | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState<FormMovimiento>(formVacio);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [catalogo, setCatalogo] = useState<CuentaContable[] | null>(null);
  const [anular, setAnular] = useState<MovimientoBancario | null>(null);
  const [anulando, setAnulando] = useState(false);

  // La búsqueda se aplica al dejar de escribir.
  useEffect(() => {
    const t = window.setTimeout(() => setQAplicada(q.trim()), 350);
    return () => window.clearTimeout(t);
  }, [q]);

  const cargar = useCallback(() => {
    setCargando(true);
    setError(null);
    bancosApi
      .listarMovimientos(cuenta.id, {
        desde: desde || undefined,
        hasta: hasta || undefined,
        conciliado: estado === "" ? undefined : estado === "si",
        q: qAplicada || undefined,
      })
      .then(setDatos)
      .catch((err) => {
        setDatos(null);
        setError(mensajeError(err));
      })
      .finally(() => setCargando(false));
  }, [cuenta.id, desde, hasta, estado, qAplicada]);

  useEffect(cargar, [cargar]);

  useEffect(() => {
    if (!abierto || catalogo) return;
    contabilidadApi
      .listarCuentas({ solo_movimiento: true, activa: true })
      .then(setCatalogo)
      .catch(() => setCatalogo([]));
  }, [abierto, catalogo]);

  const moneda = cuenta.moneda;
  const usd = moneda === "USD";

  function set<K extends keyof FormMovimiento>(campo: K, valor: FormMovimiento[K]) {
    setForm((prev) => ({ ...prev, [campo]: valor }));
    setErrorForm(null);
  }

  async function registrar() {
    const monto = Number(form.monto);
    if (!(monto > 0)) return setErrorForm("El monto debe ser mayor que cero: el tipo indica si entra o sale de la cuenta.");
    if (!form.fecha) return setErrorForm("Indique la fecha del movimiento.");
    if (form.fecha < cuenta.fecha_saldo_inicial)
      return setErrorForm(`La fecha no puede ser anterior al saldo inicial de la cuenta (${fechaLegible(cuenta.fecha_saldo_inicial)}).`);
    if (usd && !(Number(form.tasa_cambio) > 0)) return setErrorForm("Indique la tasa de cambio (RD$ por US$) para el asiento en pesos.");
    setGuardando(true);
    try {
      const m = await bancosApi.registrarMovimiento({
        cuenta_id: cuenta.id,
        fecha: form.fecha,
        tipo: form.tipo,
        monto: monto.toFixed(2),
        descripcion: form.descripcion.trim() || null,
        referencia: form.referencia.trim() || null,
        cuenta_contable_id: form.cuenta_contable_id || null,
        tasa_cambio: usd ? Number(form.tasa_cambio).toFixed(4) : null,
      });
      setAbierto(false);
      setForm(formVacio());
      mostrarAviso(`${TIPO_MOVIMIENTO_LABEL[m.tipo]} registrado`, { detalle: `${montoMoneda(m.monto, moneda)} · ${cuenta.nombre}` });
      cargar();
      recargarCuentas();
    } catch (err) {
      setErrorForm(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function confirmarAnular() {
    if (!anular) return;
    setAnulando(true);
    try {
      await bancosApi.anularMovimiento(anular.id);
      mostrarAviso("Movimiento anulado", { detalle: `${anular.descripcion} · ${montoMoneda(anular.monto, moneda)}` });
      cargar();
      recargarCuentas();
    } catch (err) {
      mostrarAviso("No se pudo anular el movimiento", { detalle: mensajeError(err), error: true });
    } finally {
      setAnular(null);
      setAnulando(false);
    }
  }

  function exportar() {
    if (!datos) return;
    const sufijo = [desde || cuenta.fecha_saldo_inicial, hasta || hoyIso()].join("_");
    descargarCsv(nombreArchivo(`extracto_${cuenta.nombre}_${cuenta.numero_enmascarado ?? ""}_${sufijo}`) + ".csv", [
      ["Fecha", "Descripcion", "Referencia", "Tipo", `Debito (${simboloMoneda(moneda)})`, `Credito (${simboloMoneda(moneda)})`, "Balance", "Conciliado"],
      ...datos.movimientos.map((m) => {
        const n = Number(m.monto);
        return [
          fechaLegible(m.fecha),
          m.descripcion,
          m.referencia,
          TIPO_MOVIMIENTO_LABEL[m.tipo] ?? m.tipo,
          n < 0 ? (-n).toFixed(2) : "",
          n > 0 ? n.toFixed(2) : "",
          m.saldo != null ? Number(m.saldo).toFixed(2) : "",
          m.anulado ? "Anulado" : m.conciliado ? "Sí" : "No",
        ];
      }),
      ["", "Saldo inicial", "", "", "", "", Number(datos.saldo_inicial).toFixed(2), ""],
    ]);
    mostrarAviso("Extracto exportado", { detalle: `${datos.movimientos.length} movimientos · listo para abrir en Excel.` });
  }

  const tipoForm = TIPOS_MOVIMIENTO_MANUAL.find((t) => t.value === form.tipo)!;
  const filtrando = Boolean(desde || hasta || estado || qAplicada);

  return (
    <>
      {abierto && (
        <section className="card no-imprimir">
          <div className="card-h">
            <h2>Registrar movimiento · {cuenta.nombre}</h2>
            <span className="hint">{tipoForm.entra ? "Entra a la cuenta" : "Sale de la cuenta"}</span>
          </div>
          <div className="card-b form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="mv-tipo">Tipo</label>
              <select id="mv-tipo" className="inp" value={form.tipo} onChange={(e) => set("tipo", e.target.value as TipoMovimientoManual)}>
                {TIPOS_MOVIMIENTO_MANUAL.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="mv-fecha">Fecha</label>
              <input id="mv-fecha" type="date" className="inp" value={form.fecha} min={cuenta.fecha_saldo_inicial} max={hoyIso()} onChange={(e) => set("fecha", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="mv-monto">
                Monto ({simboloMoneda(moneda)}) <span className="req">*</span>
              </label>
              <input
                id="mv-monto"
                type="number"
                min="0"
                step="0.01"
                className="inp"
                style={{ textAlign: "right" }}
                value={form.monto}
                onChange={(e) => set("monto", e.target.value)}
                placeholder="0.00"
              />
            </div>
            {usd && (
              <div className="fld">
                <label className="lbl" htmlFor="mv-tasa">
                  Tasa (RD$ por US$) <span className="req">*</span>
                </label>
                <input id="mv-tasa" type="number" min="0" step="0.0001" className="inp" style={{ textAlign: "right" }} value={form.tasa_cambio} onChange={(e) => set("tasa_cambio", e.target.value)} placeholder="60.2500" />
              </div>
            )}
            <div className="fld">
              <label className="lbl" htmlFor="mv-desc">Descripción</label>
              <input id="mv-desc" className="inp" value={form.descripcion} onChange={(e) => set("descripcion", e.target.value)} placeholder={tipoForm.entra ? "Depósito ventas en efectivo" : "Comisión por manejo de cuenta"} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="mv-ref">Referencia</label>
              <input id="mv-ref" className="inp mono" value={form.referencia} onChange={(e) => set("referencia", e.target.value)} placeholder="DEP-5521" />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="mv-cc">Contrapartida contable</label>
              <select id="mv-cc" className="inp" value={form.cuenta_contable_id} onChange={(e) => set("cuenta_contable_id", e.target.value)}>
                <option value="">{CONTRAPARTIDA[form.tipo]} (por defecto)</option>
                {(catalogo ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.codigo} · {c.nombre}
                  </option>
                ))}
              </select>
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
              <button type="button" className="btn btn-p" onClick={registrar} disabled={guardando}>
                {guardando ? "Registrando…" : "Registrar movimiento"}
              </button>
            </div>
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Movimientos · {cuenta.nombre}</h2>
            {datos && (
              <span className="hint">
                Entradas {montoMoneda(datos.entradas, moneda)} · Salidas {montoMoneda(Math.abs(Number(datos.salidas)), moneda)}
                {!cuenta.activa && " · cuenta inactiva"}
              </span>
            )}
          </div>
          <div className="row no-imprimir">
            <button type="button" className="btn btn-g btn-sm" onClick={onEditarCuenta}>
              Editar cuenta
            </button>
            <button type="button" className="btn btn-o btn-sm" onClick={exportar} disabled={!datos || datos.movimientos.length === 0}>
              Exportar extracto
            </button>
            <button
              type="button"
              className="btn btn-p btn-sm"
              onClick={() => {
                setErrorForm(null);
                setAbierto((v) => !v);
              }}
              disabled={!cuenta.activa}
              title={cuenta.activa ? undefined : "La cuenta está inactiva"}
            >
              {abierto ? "Cerrar formulario" : "+ Movimiento"}
            </button>
          </div>
        </div>
        <div className="bk-filtros no-imprimir">
          <div className="fld">
            <label className="lbl" htmlFor="mf-desde">Desde</label>
            <input id="mf-desde" type="date" className="inp" value={desde} max={hasta || undefined} onChange={(e) => setDesde(e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="mf-hasta">Hasta</label>
            <input id="mf-hasta" type="date" className="inp" value={hasta} min={desde || undefined} onChange={(e) => setHasta(e.target.value)} />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="mf-est">Conciliación</label>
            <select id="mf-est" className="inp" value={estado} onChange={(e) => setEstado(e.target.value as "" | "si" | "no")}>
              <option value="">Todos</option>
              <option value="no">Sin conciliar</option>
              <option value="si">Conciliados</option>
            </select>
          </div>
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar descripción o referencia" />
        </div>

        {error && (
          <div className="card-b" style={{ paddingTop: 0 }}>
            <AvisoError titulo="No se pudieron cargar los movimientos." mensaje={error} onReintentar={cargar} />
          </div>
        )}
        {cargando && !datos && <Cargando />}
        {datos && datos.movimientos.length === 0 && !filtrando && (
          <Vacio titulo="Sin movimientos todavía" texto={`Los depósitos, retiros, transferencias y los cobros y pagos registrados con esta cuenta aparecerán aquí. Saldo inicial: ${montoMoneda(cuenta.saldo_inicial, moneda)} al ${fechaLegible(cuenta.fecha_saldo_inicial)}.`} />
        )}
        {datos && datos.movimientos.length === 0 && filtrando && (
          <Vacio titulo="Sin resultados" texto="No hay movimientos con estos filtros.">
            <button
              type="button"
              className="btn btn-o btn-sm"
              onClick={() => {
                setDesde("");
                setHasta("");
                setEstado("");
                setQ("");
              }}
            >
              Ver todos
            </button>
          </Vacio>
        )}
        {datos && datos.movimientos.length > 0 && (
          <div className="tw" style={{ opacity: cargando ? 0.6 : 1 }}>
            <table className="tbl" style={{ minWidth: 900 }}>
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Descripción</th>
                  <th>Referencia</th>
                  <th className="num">Débito</th>
                  <th className="num">Crédito</th>
                  <th className="num">Balance</th>
                  <th style={{ textAlign: "right" }}>
                    Acciones
                  </th>
                </tr>
              </thead>
              <tbody>
                {datos.movimientos.map((m) => {
                  const n = Number(m.monto);
                  return (
                    <tr key={`${m.origen}-${m.id}`} className={m.anulado ? "bk-anulado" : undefined}>
                      <td className="mut nowrap">{fechaLegible(m.fecha)}</td>
                      <td>
                        {m.descripcion}
                        <div className="hint">
                          {TIPO_MOVIMIENTO_LABEL[m.tipo] ?? m.tipo}
                          {m.anulado ? " · anulado" : m.conciliado ? " · conciliado" : ""}
                        </div>
                      </td>
                      <td className="mono mut">{m.referencia ?? ""}</td>
                      <td className="num bk-deb">{n < 0 ? formatoMonto(-n) : ""}</td>
                      <td className="num bk-cre">{n > 0 ? formatoMonto(n) : ""}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {m.saldo != null ? formatoMonto(m.saldo) : ""}
                      </td>
                      <td>
                        <div className="fila-acc">
                          {sePuedeAnular(m) && (
                            <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => setAnular(m)}>
                              Anular
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
                <tr>
                  <td className="mut nowrap">{fechaLegible(datos.desde ?? cuenta.fecha_saldo_inicial)}</td>
                  <td className="mut">Saldo inicial</td>
                  <td />
                  <td />
                  <td />
                  <td className="num" style={{ fontWeight: 600 }}>
                    {formatoMonto(datos.saldo_inicial)}
                  </td>
                  <td />
                </tr>
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={3}>Saldo final{datos.hasta ? ` al ${fechaLegible(datos.hasta)}` : ""}</td>
                  <td className="num">{formatoMonto(Math.abs(Number(datos.salidas)))}</td>
                  <td className="num">{formatoMonto(datos.entradas)}</td>
                  <td className="num">{formatoMonto(datos.saldo_final)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </section>

      {anular && (
        <Modal
          titulo="¿Anular este movimiento?"
          onCerrar={() => setAnular(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setAnular(null)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarAnular} disabled={anulando}>
                {anulando ? "Anulando…" : "Sí, anular"}
              </button>
            </>
          }
        >
          <span>
            {TIPO_MOVIMIENTO_LABEL[anular.tipo]} del {fechaLegible(anular.fecha)} por <b style={{ color: "#111827" }}>{montoMoneda(anular.monto, moneda)}</b> ({anular.descripcion}). El
            saldo de la cuenta se recalcula y su asiento se anula al volver a generar los asientos del período.
          </span>
        </Modal>
      )}
    </>
  );
}
