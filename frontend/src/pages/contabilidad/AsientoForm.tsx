// Asiento contable: "Nuevo asiento manual" de Contabilidad.dc.html (líneas débito/crédito con el indicador
// Cuadrado / Descuadre y "Contabilizar asiento") en /contabilidad/asientos/nuevo, y la ficha de un asiento en
// /contabilidad/asientos/:id (los borradores manuales se editan; los demás se consultan y, si procede, se anulan).
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { Asiento, AsientoPayload, CuentaContable } from "../../lib/contabilidadApi";
import { formatoMonto } from "../../lib/marca";
import { AvisoError, Cargando, Modal } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { centavos, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import "../ingresos/ingresos.css";
import { ModalAnular } from "./Asientos";
import { ContabilidadEncabezado } from "./Contabilidad";
import type { MostrarAviso } from "./Contabilidad";
import "./contabilidad.css";
import { ESTADO_ASIENTO, etiquetaCuentaContable, sePuedeAnular } from "./contabilidadUtil";

interface Linea {
  clave: number;
  cuenta_id: string;
  descripcion: string;
  debito: string;
  credito: string;
}

let siguienteClave = 1;
function lineaVacia(): Linea {
  return { clave: siguienteClave++, cuenta_id: "", descripcion: "", debito: "", credito: "" };
}

export function AsientoForm() {
  const { id } = useParams<{ id: string }>();
  // El aviso vive fuera de la pantalla: sobrevive al pasar de /nuevo a /:id después de guardar.
  const [toast, mostrarAviso] = useAviso();
  return (
    <>
      <AsientoPantalla key={id ?? "nuevo"} id={id} mostrarAviso={mostrarAviso} />
      {toast}
    </>
  );
}

function AsientoPantalla({ id, mostrarAviso }: { id?: string; mostrarAviso: MostrarAviso }) {
  const navigate = useNavigate();
  const [asiento, setAsiento] = useState<Asiento | null>(null);
  const [cargando, setCargando] = useState(!!id);
  const [error, setError] = useState<string | null>(null);
  const [catalogo, setCatalogo] = useState<CuentaContable[]>([]);
  const [errorCatalogo, setErrorCatalogo] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    const [cat, as] = await Promise.allSettled([
      contabilidadApi.listarCuentas({ solo_movimiento: true, activa: true }),
      id ? contabilidadApi.obtenerAsiento(id) : Promise.resolve(null),
    ]);
    if (cat.status === "fulfilled") {
      setCatalogo(cat.value);
      setErrorCatalogo(null);
    } else setErrorCatalogo(mensajeError(cat.reason));
    if (as.status === "fulfilled") {
      setAsiento(as.value);
      setError(null);
    } else setError(mensajeError(as.reason));
    setCargando(false);
  }, [id]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const editable = !id || (asiento?.estado === "borrador" && asiento.origen === "manual");
  const titulo = id ? (asiento ? `Asiento ${asiento.numero}` : "Asiento") : "Nuevo asiento manual";

  return (
    <div className="content">
      <ContabilidadEncabezado
        titulo={titulo}
        pestana="asientos"
        acciones={
          <Link className="btn btn-o" to="/contabilidad/asientos">
            Volver a asientos
          </Link>
        }
      />
      {errorCatalogo && editable && <AvisoError titulo="No se pudo cargar el catálogo de cuentas." mensaje={errorCatalogo} onReintentar={() => void cargar()} />}
      {error && <AvisoError titulo="No se pudo abrir el asiento." mensaje={error} onReintentar={() => void cargar()} />}
      {cargando && (
        <section className="card">
          <Cargando />
        </section>
      )}
      {!cargando && !error && editable && (
        <FormularioAsiento
          asiento={asiento}
          catalogo={catalogo}
          onGuardado={(a, contabilizado) => {
            mostrarAviso(contabilizado ? "Asiento contabilizado" : "Borrador guardado", { detalle: `${a.numero} · ${a.concepto}` });
            if (!id || id !== a.id) navigate(`/contabilidad/asientos/${a.id}`, { replace: !id });
            else setAsiento(a);
          }}
          onEliminado={() => {
            navigate("/contabilidad/asientos");
          }}
        />
      )}
      {!cargando && asiento && !editable && <FichaAsiento asiento={asiento} onCambio={setAsiento} mostrarAviso={mostrarAviso} />}
    </div>
  );
}

function FormularioAsiento({
  asiento,
  catalogo,
  onGuardado,
  onEliminado,
}: {
  asiento: Asiento | null;
  catalogo: CuentaContable[];
  onGuardado: (a: Asiento, contabilizado: boolean) => void;
  onEliminado: () => void;
}) {
  const [fecha, setFecha] = useState(asiento?.fecha ?? hoyIso());
  const [concepto, setConcepto] = useState(asiento?.concepto ?? "");
  const [referencia, setReferencia] = useState(asiento?.referencia ?? "");
  const [lineas, setLineas] = useState<Linea[]>(() =>
    asiento
      ? asiento.lineas.map((l) => ({
          clave: siguienteClave++,
          cuenta_id: l.cuenta_id,
          descripcion: l.descripcion ?? "",
          debito: Number(l.debito) ? String(Number(l.debito)) : "",
          credito: Number(l.credito) ? String(Number(l.credito)) : "",
        }))
      : [lineaVacia(), lineaVacia()],
  );
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState<"" | "borrador" | "contabilizar">("");
  const [eliminar, setEliminar] = useState(false);

  // Las cuentas de las líneas que ya no estén en la lista (inactivas) se muestran igual.
  const extra = useMemo(
    () => (asiento?.lineas ?? []).filter((l) => !catalogo.some((c) => c.id === l.cuenta_id)).map((l) => ({ id: l.cuenta_id, codigo: l.cuenta_codigo, nombre: l.cuenta_nombre })),
    [asiento, catalogo],
  );

  const totalD = centavos(lineas.reduce((s, l) => s + (Number(l.debito) || 0), 0));
  const totalC = centavos(lineas.reduce((s, l) => s + (Number(l.credito) || 0), 0));
  const cuadrado = totalD === totalC && totalD > 0;

  function setLinea(clave: number, campo: keyof Omit<Linea, "clave">, valor: string) {
    setLineas((prev) =>
      prev.map((l) => {
        if (l.clave !== clave) return l;
        const n = { ...l, [campo]: valor };
        // Una línea lleva débito o crédito, no los dos.
        if (campo === "debito" && Number(valor) > 0) n.credito = "";
        if (campo === "credito" && Number(valor) > 0) n.debito = "";
        return n;
      }),
    );
    setError(null);
  }

  function validar(contabilizar: boolean): string | null {
    if (!concepto.trim()) return "Indique el concepto del asiento.";
    if (!fecha) return "Indique la fecha del asiento.";
    const usadas = lineas.filter((l) => l.cuenta_id || Number(l.debito) || Number(l.credito));
    if (usadas.length < 2) return "El asiento necesita al menos dos líneas.";
    for (const [i, l] of usadas.entries()) {
      if (!l.cuenta_id) return `Elija la cuenta de la línea ${i + 1}.`;
      const d = Number(l.debito) || 0;
      const c = Number(l.credito) || 0;
      if (d < 0 || c < 0) return `La línea ${i + 1} tiene un monto negativo.`;
      if ((d > 0) === (c > 0)) return `La línea ${i + 1} debe llevar un débito o un crédito (uno solo).`;
    }
    if (contabilizar && !cuadrado) return `El asiento no cuadra: débitos RD$ ${formatoMonto(totalD)}, créditos RD$ ${formatoMonto(totalC)}.`;
    return null;
  }

  async function guardar(contabilizar: boolean) {
    const e = validar(contabilizar);
    if (e) return setError(e);
    const payload: AsientoPayload = {
      fecha,
      concepto: concepto.trim(),
      referencia: referencia.trim() || null,
      contabilizar,
      lineas: lineas
        .filter((l) => l.cuenta_id)
        .map((l) => ({
          cuenta_id: l.cuenta_id,
          descripcion: l.descripcion.trim() || null,
          ...(Number(l.debito) > 0 ? { debito: Number(l.debito).toFixed(2) } : { credito: Number(l.credito).toFixed(2) }),
        })),
    };
    setGuardando(contabilizar ? "contabilizar" : "borrador");
    try {
      let a = asiento ? await contabilidadApi.actualizarAsiento(asiento.id, { ...payload, contabilizar: false }) : await contabilidadApi.crearAsiento(payload);
      if (asiento && contabilizar) a = await contabilidadApi.contabilizarAsiento(a.id);
      onGuardado(a, contabilizar);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setGuardando("");
    }
  }

  async function confirmarEliminar() {
    if (!asiento) return;
    try {
      await contabilidadApi.eliminarAsiento(asiento.id);
      onEliminado();
    } catch (err) {
      setEliminar(false);
      setError(mensajeError(err));
    }
  }

  return (
    <>
      <section className="card">
        <div className="card-h">
          <h2>{asiento ? `Borrador ${asiento.numero}` : "Datos del asiento"}</h2>
          {asiento && <span className="bd b-borr">Borrador</span>}
        </div>
        <div className="card-b form-grid">
          <div className="fld">
            <label className="lbl" htmlFor="as-fecha">Fecha</label>
            <input id="as-fecha" type="date" className="inp" value={fecha} onChange={(e) => setFecha(e.target.value)} />
          </div>
          <div className="fld" style={{ gridColumn: "span 2" }}>
            <label className="lbl" htmlFor="as-con">
              Concepto <span className="req">*</span>
            </label>
            <input id="as-con" className="inp" value={concepto} onChange={(e) => setConcepto(e.target.value)} placeholder="Asiento de apertura, ajuste de inventario final…" />
          </div>
          <div className="fld">
            <label className="lbl" htmlFor="as-ref">Referencia</label>
            <input id="as-ref" className="inp mono" value={referencia} onChange={(e) => setReferencia(e.target.value)} placeholder="Opcional" />
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-h">
          <h2>{asiento ? "Líneas del asiento" : "Nuevo asiento manual"}</h2>
          <span className={cuadrado ? "bd b-acep" : "bd b-proc"}>{cuadrado ? "Cuadrado" : `Descuadre: ${formatoMonto(Math.abs(totalD - totalC))}`}</span>
        </div>
        <div className="tw">
          <table className="tbl ct-lineas" style={{ minWidth: 820 }}>
            <thead>
              <tr>
                <th>Cuenta</th>
                <th>Descripción</th>
                <th style={{ width: 170 }}>Débito</th>
                <th style={{ width: 170 }}>Crédito</th>
                <th style={{ width: 48 }} aria-label="Quitar" />
              </tr>
            </thead>
            <tbody>
              {lineas.map((l, i) => (
                <tr key={l.clave}>
                  <td>
                    <select className="inp inp-sm" value={l.cuenta_id} onChange={(e) => setLinea(l.clave, "cuenta_id", e.target.value)} aria-label={`Cuenta de la línea ${i + 1}`}>
                      <option value="">Elija la cuenta…</option>
                      {extra.map((c) => (
                        <option key={c.id} value={c.id}>
                          {etiquetaCuentaContable(c)}
                        </option>
                      ))}
                      {catalogo.map((c) => (
                        <option key={c.id} value={c.id}>
                          {etiquetaCuentaContable(c)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input className="inp inp-sm" style={{ textAlign: "left" }} value={l.descripcion} onChange={(e) => setLinea(l.clave, "descripcion", e.target.value)} aria-label={`Descripción de la línea ${i + 1}`} />
                  </td>
                  <td>
                    <input type="number" min="0" step="0.01" className="inp inp-sm" value={l.debito} onChange={(e) => setLinea(l.clave, "debito", e.target.value)} aria-label={`Débito de la línea ${i + 1}`} placeholder="0.00" />
                  </td>
                  <td>
                    <input type="number" min="0" step="0.01" className="inp inp-sm" value={l.credito} onChange={(e) => setLinea(l.clave, "credito", e.target.value)} aria-label={`Crédito de la línea ${i + 1}`} placeholder="0.00" />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="ib"
                      style={{ width: 32, height: 32 }}
                      onClick={() => setLineas((prev) => prev.filter((x) => x.clave !== l.clave))}
                      disabled={lineas.length <= 2}
                      aria-label={`Quitar la línea ${i + 1}`}
                    >
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                        <path d="M18 6 6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={2}>Totales</td>
                <td className="num">{formatoMonto(totalD)}</td>
                <td className="num">{formatoMonto(totalC)}</td>
                <td />
              </tr>
            </tfoot>
          </table>
        </div>
        <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div>
            <button type="button" className="btn btn-o btn-sm" onClick={() => setLineas((prev) => [...prev, lineaVacia()])}>
              + Agregar línea
            </button>
          </div>
          {error && (
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span>
                <b>{error}</b>
              </span>
            </div>
          )}
        </div>
        <div className="row" style={{ justifyContent: "flex-end", padding: "14px 20px", borderTop: "1px solid #EEF0F2", flexWrap: "wrap" }}>
          {asiento && (
            <button type="button" className="btn btn-g" style={{ color: "#B91C1C", marginRight: "auto" }} onClick={() => setEliminar(true)}>
              Eliminar borrador
            </button>
          )}
          <button type="button" className="btn btn-o" onClick={() => guardar(false)} disabled={!!guardando}>
            {guardando === "borrador" ? "Guardando…" : "Guardar borrador"}
          </button>
          <button type="button" className="btn btn-p" onClick={() => guardar(true)} disabled={!cuadrado || !!guardando}>
            {guardando === "contabilizar" ? "Contabilizando…" : "Contabilizar asiento"}
          </button>
        </div>
      </section>

      {eliminar && asiento && (
        <Modal
          titulo={`¿Eliminar el borrador ${asiento.numero}?`}
          onCerrar={() => setEliminar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setEliminar(false)}>
                Cancelar
              </button>
              <button type="button" className="btn btn-d" onClick={confirmarEliminar}>
                Sí, eliminar
              </button>
            </>
          }
        >
          <span>El borrador se elimina por completo; los asientos contabilizados no se borran, se anulan.</span>
        </Modal>
      )}
    </>
  );
}

function FichaAsiento({
  asiento,
  onCambio,
  mostrarAviso,
}: {
  asiento: Asiento;
  onCambio: (a: Asiento) => void;
  mostrarAviso: MostrarAviso;
}) {
  const [anular, setAnular] = useState(false);
  const est = ESTADO_ASIENTO[asiento.estado] ?? { label: asiento.estado, badge: "bd b-borr" };

  return (
    <>
      <section className="card">
        <div className="card-h">
          <div>
            <h2>
              {asiento.numero} · {asiento.concepto}
            </h2>
            <span className="hint">
              {fechaLegible(asiento.fecha)} · {asiento.origen_etiqueta}
              {asiento.referencia ? ` · ${asiento.referencia}` : ""}
              {asiento.automatico ? " · generado desde el documento" : ""}
            </span>
          </div>
          <div className="row">
            <span className={est.badge}>{est.label}</span>
            {sePuedeAnular(asiento) && (
              <button type="button" className="btn btn-o btn-sm" style={{ color: "#B91C1C" }} onClick={() => setAnular(true)}>
                Anular
              </button>
            )}
          </div>
        </div>
        {asiento.estado === "anulado" && (
          <div className="card-b" style={{ paddingTop: 0 }}>
            <div className="al al-d">
              <IconAlerta />
              <span>
                Anulado el {fechaLegible(asiento.anulado_en)}
                {asiento.motivo_anulacion ? ` · ${asiento.motivo_anulacion}` : ""}. No suma en los reportes.
              </span>
            </div>
          </div>
        )}
        {asiento.automatico && asiento.estado === "contabilizado" && asiento.origen !== "depreciacion" && (
          <div className="card-b" style={{ paddingTop: 0 }}>
            <div className="al al-i">
              <IconAlerta />
              <span>Los asientos automáticos no se editan: corrija el documento de origen y vuelva a generar los asientos del período.</span>
            </div>
          </div>
        )}
        <div className="tw">
          <table className="tbl" style={{ minWidth: 680 }}>
            <thead>
              <tr>
                <th>Cuenta</th>
                <th>Descripción</th>
                <th className="num">Débito</th>
                <th className="num">Crédito</th>
              </tr>
            </thead>
            <tbody>
              {asiento.lineas.map((l) => (
                <tr key={l.id}>
                  <td>
                    <span className="mono mut">{l.cuenta_codigo}</span> {l.cuenta_nombre}
                  </td>
                  <td className="mut">{l.descripcion ?? ""}</td>
                  <td className="num">{Number(l.debito) ? formatoMonto(l.debito) : ""}</td>
                  <td className="num">{Number(l.credito) ? formatoMonto(l.credito) : ""}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={2}>Totales{asiento.cuadrado ? "" : " · descuadrado"}</td>
                <td className="num">{formatoMonto(asiento.total_debito)}</td>
                <td className="num">{formatoMonto(asiento.total_credito)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </section>
      {anular && (
        <ModalAnular
          asiento={asiento}
          onCerrar={() => setAnular(false)}
          onAnulado={() => {
            setAnular(false);
            mostrarAviso("Asiento anulado", { detalle: `${asiento.numero} · ${asiento.concepto}` });
            void contabilidadApi.obtenerAsiento(asiento.id).then(onCambio, () => undefined);
          }}
        />
      )}
    </>
  );
}
