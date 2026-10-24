// Importar el estado de cuenta (CSV): vista previa con las columnas detectadas, mapeo editable (columna de
// cada campo, formato de fecha, separador decimal y signo de la columna única) y la importación a la cuenta.
import { useCallback, useEffect, useState } from "react";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria, MapeoExtracto, PrevisualizacionExtracto, ResultadoImportacion } from "../../lib/bancosApi";
import { formatoMonto } from "../../lib/marca";
import { Cargando, Modal } from "../ingresos/IngresosShell";
import { fechaLegible, mensajeError } from "../ingresos/ingresosUtil";
import { etiquetaCuenta } from "./bancosUtil";

type Campo = "fecha" | "descripcion" | "referencia" | "debito" | "credito" | "monto" | "saldo" | "tipo";

const CAMPOS: { id: Campo; label: string; ayuda?: string }[] = [
  { id: "fecha", label: "Fecha" },
  { id: "descripcion", label: "Descripción" },
  { id: "referencia", label: "Referencia" },
  { id: "debito", label: "Débito (sale)" },
  { id: "credito", label: "Crédito (entra)" },
  { id: "monto", label: "Monto con signo", ayuda: "Si el banco usa una sola columna" },
  { id: "tipo", label: "Columna D/C", ayuda: "Indica débito o crédito" },
  { id: "saldo", label: "Balance" },
];

const SEPARADOR_LABEL: Record<string, string> = { ",": "coma", ";": "punto y coma", tabulador: "tabulador", "|": "barra vertical" };

interface Opciones {
  columnas: Record<Campo, string>;
  formato_fecha: "" | "DD/MM/AAAA" | "MM/DD/AAAA" | "AAAA-MM-DD";
  separador_decimal: "" | "." | ",";
  signo_monto: "credito_positivo" | "debito_positivo";
}

/** Columnas detectadas por el backend (nombre del encabezado) como posiciones para los selectores. */
function opcionesDe(p: PrevisualizacionExtracto, previas?: Opciones): Opciones {
  const columnas = {} as Record<Campo, string>;
  for (const c of CAMPOS) {
    const nombre = p.mapeo[c.id];
    const i = nombre == null ? -1 : p.columnas.indexOf(nombre);
    columnas[c.id] = i >= 0 ? String(i) : "";
  }
  return { formato_fecha: "", separador_decimal: "", signo_monto: "credito_positivo", ...previas, columnas };
}

/** Mapeo explícito: cada campo por posición ("" = no usar esa columna), más el formato elegido. */
function mapeoDe(o: Opciones, filaEncabezado: number): MapeoExtracto {
  const m: MapeoExtracto = { fila_encabezado: filaEncabezado };
  for (const c of CAMPOS) m[c.id] = o.columnas[c.id] === "" ? "" : Number(o.columnas[c.id]);
  if (o.formato_fecha) m.formato_fecha = o.formato_fecha;
  if (o.separador_decimal) m.separador_decimal = o.separador_decimal;
  if (o.columnas.monto !== "") m.signo_monto = o.signo_monto;
  return m;
}

export function ImportarExtracto({
  cuenta,
  archivo,
  onCerrar,
  onImportado,
}: {
  cuenta: CuentaBancaria;
  archivo: File;
  onCerrar: () => void;
  onImportado: (r: ResultadoImportacion) => void;
}) {
  const [vista, setVista] = useState<PrevisualizacionExtracto | null>(null);
  const [opciones, setOpciones] = useState<Opciones | null>(null);
  const [editado, setEditado] = useState(false);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saldoFinal, setSaldoFinal] = useState("");
  const [importando, setImportando] = useState(false);

  const previsualizar = useCallback(
    async (mapeo?: MapeoExtracto, previas?: Opciones) => {
      setCargando(true);
      setError(null);
      try {
        const p = await bancosApi.previsualizarExtracto(archivo, mapeo);
        setVista(p);
        setOpciones(opcionesDe(p, previas));
        setEditado(false);
      } catch (err) {
        setError(mensajeError(err));
      } finally {
        setCargando(false);
      }
    },
    [archivo],
  );

  useEffect(() => {
    void previsualizar();
  }, [previsualizar]);

  function cambiarColumna(campo: Campo, valor: string) {
    if (!opciones) return;
    setOpciones({ ...opciones, columnas: { ...opciones.columnas, [campo]: valor } });
    setEditado(true);
  }

  function cambiar<K extends Exclude<keyof Opciones, "columnas">>(campo: K, valor: Opciones[K]) {
    if (!opciones) return;
    setOpciones({ ...opciones, [campo]: valor });
    setEditado(true);
  }

  async function importar() {
    if (!vista || !opciones) return;
    if (saldoFinal.trim() && !Number.isFinite(Number(saldoFinal))) return setError("El saldo final no es un monto válido.");
    setImportando(true);
    setError(null);
    try {
      const res = await bancosApi.importarExtracto(cuenta.id, archivo, {
        mapeo: editado || opciones.formato_fecha || opciones.separador_decimal ? mapeoDe(opciones, vista.fila_encabezado) : undefined,
        saldo_final: saldoFinal.trim() ? Number(saldoFinal).toFixed(2) : undefined,
      });
      onImportado(res);
    } catch (err) {
      setError(mensajeError(err));
      setImportando(false);
    }
  }

  const sinMonto = opciones ? opciones.columnas.monto === "" && opciones.columnas.debito === "" && opciones.columnas.credito === "" : true;

  return (
    <Modal
      titulo="Importar estado de cuenta"
      onCerrar={onCerrar}
      ancho={860}
      pie={
        <>
          <button type="button" className="btn btn-o" onClick={onCerrar}>
            Cancelar
          </button>
          {editado && (
            <button type="button" className="btn btn-o" onClick={() => opciones && vista && void previsualizar(mapeoDe(opciones, vista.fila_encabezado), opciones)} disabled={cargando}>
              Actualizar vista previa
            </button>
          )}
          <button type="button" className="btn btn-p" onClick={importar} disabled={!vista || cargando || importando || editado || sinMonto || vista.total_lineas === 0}>
            {importando ? "Importando…" : vista ? `Importar ${vista.total_lineas} ${vista.total_lineas === 1 ? "línea" : "líneas"}` : "Importar"}
          </button>
        </>
      }
    >
      <span>
        <b style={{ color: "#111827" }}>{archivo.name}</b> → {etiquetaCuenta(cuenta)}.{" "}
        {vista && `Separador: ${SEPARADOR_LABEL[vista.separador] ?? vista.separador} · encabezado en la fila ${vista.fila_encabezado} · ${vista.total_lineas} líneas con fecha.`} Volver a
        subir el mismo archivo no duplica las líneas.
      </span>

      {cargando && !vista && <Cargando />}

      {error && (
        <div className="al al-d" role="alert">
          <IconAlerta />
          <span>
            <b>{error}</b>
            {!vista && " Revise que sea el CSV del estado de cuenta (no el PDF) y que tenga una fila de encabezado."}
          </span>
        </div>
      )}

      {vista && opciones && (
        <>
          <div className="bk-mapeo" role="group" aria-label="Columnas del archivo">
            {CAMPOS.map((c) => (
              <div key={c.id} className="fld">
                <label className="lbl" htmlFor={`mp-${c.id}`}>
                  {c.label}
                </label>
                <select id={`mp-${c.id}`} className="inp" value={opciones.columnas[c.id]} onChange={(e) => cambiarColumna(c.id, e.target.value)}>
                  <option value="">— Sin columna —</option>
                  {vista.columnas.map((col, i) => (
                    <option key={i} value={String(i)}>
                      {col || `Columna ${i + 1}`}
                    </option>
                  ))}
                </select>
                {c.ayuda && <span className="hint">{c.ayuda}</span>}
              </div>
            ))}
            <div className="fld">
              <label className="lbl" htmlFor="mp-ff">Formato de fecha</label>
              <select id="mp-ff" className="inp" value={opciones.formato_fecha} onChange={(e) => cambiar("formato_fecha", e.target.value as Opciones["formato_fecha"])}>
                <option value="">Detectar (día primero)</option>
                <option value="DD/MM/AAAA">DD/MM/AAAA</option>
                <option value="MM/DD/AAAA">MM/DD/AAAA</option>
                <option value="AAAA-MM-DD">AAAA-MM-DD</option>
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="mp-dec">Separador decimal</label>
              <select id="mp-dec" className="inp" value={opciones.separador_decimal} onChange={(e) => cambiar("separador_decimal", e.target.value as Opciones["separador_decimal"])}>
                <option value="">Detectar</option>
                <option value=".">Punto (1,250.00)</option>
                <option value=",">Coma (1.250,00)</option>
              </select>
            </div>
            {opciones.columnas.monto !== "" && (
              <div className="fld">
                <label className="lbl" htmlFor="mp-signo">Monto positivo es</label>
                <select id="mp-signo" className="inp" value={opciones.signo_monto} onChange={(e) => cambiar("signo_monto", e.target.value as Opciones["signo_monto"])}>
                  <option value="credito_positivo">Crédito (entra)</option>
                  <option value="debito_positivo">Débito (sale)</option>
                </select>
              </div>
            )}
          </div>
          {sinMonto && (
            <div className="al al-w" role="status">
              <IconAlerta />
              <span>Indique la columna del monto, o las de débito y crédito.</span>
            </div>
          )}
          {editado && <span className="hint">Cambió el mapeo: actualice la vista previa para revisar cómo quedan las líneas antes de importar.</span>}

          <div className="tw" style={{ border: "1px solid #E5E7EB", borderRadius: 10, opacity: cargando ? 0.6 : 1 }}>
            <table className="tbl" style={{ minWidth: 640 }}>
              <thead>
                <tr>
                  <th>Fila</th>
                  <th>Fecha</th>
                  <th>Descripción</th>
                  <th>Referencia</th>
                  <th className="num">Monto</th>
                  <th className="num">Balance</th>
                </tr>
              </thead>
              <tbody>
                {vista.lineas.map((l) => (
                  <tr key={l.fila}>
                    <td className="mut">{l.fila}</td>
                    <td className="nowrap">{fechaLegible(l.fecha)}</td>
                    <td>{l.descripcion}</td>
                    <td className="mono mut">{l.referencia ?? ""}</td>
                    <td className={Number(l.monto) < 0 ? "num bk-deb" : "num bk-cre"}>{formatoMonto(l.monto)}</td>
                    <td className="num">{l.saldo != null ? formatoMonto(l.saldo) : ""}</td>
                  </tr>
                ))}
                {vista.lineas.length === 0 && (
                  <tr>
                    <td colSpan={6} className="mut" style={{ textAlign: "center", padding: "20px 14px" }}>
                      No se leyó ninguna línea con estas columnas.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {vista.total_lineas > vista.lineas.length && <span className="hint">Se muestran las primeras {vista.lineas.length} de {vista.total_lineas} líneas.</span>}
          {vista.errores.length > 0 && (
            <div className="al al-w" role="status">
              <IconAlerta />
              <div>
                <b>
                  {vista.errores.length} {vista.errores.length === 1 ? "fila no se podrá importar" : "filas no se podrán importar"}:
                </b>
                <ul className="bk-lista">
                  {vista.errores.slice(0, 6).map((e) => (
                    <li key={e.fila}>
                      Fila {e.fila}: {e.motivo}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
          <div className="fld" style={{ maxWidth: 320 }}>
            <label className="lbl" htmlFor="mp-saldo-final">Saldo final del estado de cuenta ({cuenta.moneda === "USD" ? "US$" : "RD$"})</label>
            <input id="mp-saldo-final" type="number" step="0.01" className="inp" style={{ textAlign: "right" }} value={saldoFinal} onChange={(e) => setSaldoFinal(e.target.value)} placeholder="Opcional" />
            <span className="hint">Si lo indica, se usa como saldo según el banco al cerrar la conciliación.</span>
          </div>
        </>
      )}
    </Modal>
  );
}
