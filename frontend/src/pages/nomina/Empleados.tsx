// Nómina › Empleados (vista "empleados" de Nomina.dc.html): lista con búsqueda, alta y edición,
// dar de baja / reactivar, importación CSV y el estimado de prestaciones laborales.
import { useCallback, useEffect, useMemo, useState } from "react";
import { formatoMonto } from "../../lib/marca";
import { nominaApi, type Empleado, type EmpleadoPayload, type Prestaciones, type ResultadoImportacion } from "../../lib/nominaApi";
import { AvisoError, Buscador, Cargando, Modal, SinResultados, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { descargarCsv, fechaLegible, hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { Encabezado, NominaShell } from "./NominaShell";
import { formatoCedula, payloadEmpleado, refrescarAvisos } from "./nominaUtil";

const VACIO: EmpleadoPayload = {
  cedula: "", nombre: "", cargo: "", departamento: "", fecha_ingreso: hoyIso(), salario_mensual: "", tipo_pago: "mensual",
  banco: "", cuenta_bancaria: "", tipo_cuenta: "ahorro", afp: "", ars: "", correo: "", estado: "activo", fecha_salida: null,
};

const BANCOS = ["Banco Popular", "Banreservas", "Banco BHD", "Scotiabank", "Banco Santa Cruz", "Banesco", "Banco Promerica", "Banco Caribe", "Asociación Popular (APAP)", "Asociación Cibao (ACAP)"];
const AFPS = ["AFP Popular", "AFP Crecer", "AFP Reservas", "AFP Siembra", "AFP JMMB-BDI", "AFP Romana"];
const ARSS = ["ARS Humano", "ARS Universal", "ARS Senasa", "ARS Palic", "ARS Mapfre Salud", "ARS Futuro", "ARS Monumental", "ARS Renacer"];

const PLANTILLA = [
  ["cedula", "nombre", "cargo", "departamento", "fecha_ingreso", "salario_mensual", "tipo_pago", "banco", "cuenta_bancaria", "tipo_cuenta", "afp", "ars", "correo"],
  ["402-2345678-1", "Ramón Peralta Núñez", "Vendedor", "Ventas", "2024-03-01", "45000.00", "mensual", "Banco Popular", "784-123456-7", "ahorro", "AFP Popular", "ARS Humano", ""],
];

export function EmpleadosPage() {
  const [empleados, setEmpleados] = useState<Empleado[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState<"activo" | "inactivo" | "">("activo");
  const [form, setForm] = useState<EmpleadoPayload | null>(null);
  const [editando, setEditando] = useState<Empleado | null>(null);
  const [errorForm, setErrorForm] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [baja, setBaja] = useState<Empleado | null>(null);
  const [fechaBaja, setFechaBaja] = useState(hoyIso());
  const [importar, setImportar] = useState(false);
  const [textoCsv, setTextoCsv] = useState("");
  const [resultado, setResultado] = useState<ResultadoImportacion | null>(null);
  const [errorImp, setErrorImp] = useState<string | null>(null);
  const [prest, setPrest] = useState<{ e: Empleado; fecha: string; desahucio: boolean; r: Prestaciones | null; error: string | null } | null>(null);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async () => {
    try {
      setEmpleados(await nominaApi.empleados());
      setError(null);
    } catch (err) {
      setEmpleados([]);
      setError(mensajeError(err));
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const activos = (empleados ?? []).filter((e) => e.estado === "activo").length;
  const filas = useMemo(() => {
    const t = q.trim().toLowerCase();
    const digitos = t.replace(/\D/g, "");
    return (empleados ?? []).filter(
      (e) =>
        (!estado || e.estado === estado) &&
        (!t || [e.nombre, e.cargo, e.departamento].join(" ").toLowerCase().includes(t) || (digitos.length > 2 && e.cedula.includes(digitos))),
    );
  }, [empleados, q, estado]);

  function abrir(e?: Empleado) {
    setErrorForm(null);
    setEditando(e ?? null);
    setForm(e ? payloadEmpleado(e) : { ...VACIO, fecha_ingreso: hoyIso() });
  }

  function campo<K extends keyof EmpleadoPayload>(k: K, v: EmpleadoPayload[K]) {
    setForm((f) => (f ? { ...f, [k]: v } : f));
    setErrorForm(null);
  }

  async function guardar() {
    if (!form) return;
    if (!form.nombre.trim()) return setErrorForm("Escriba el nombre completo.");
    if (form.cedula.replace(/\D/g, "").length !== 11) return setErrorForm("La cédula lleva 11 dígitos.");
    if (!(Number(form.salario_mensual) > 0)) return setErrorForm("Indique el salario mensual.");
    setGuardando(true);
    const limpio = (v: string | null) => (v && v.trim()) || null;
    const datos: EmpleadoPayload = {
      ...form, cargo: limpio(form.cargo), departamento: limpio(form.departamento), banco: limpio(form.banco),
      cuenta_bancaria: limpio(form.cuenta_bancaria), tipo_cuenta: form.cuenta_bancaria ? form.tipo_cuenta : null,
      afp: limpio(form.afp), ars: limpio(form.ars), correo: limpio(form.correo),
    };
    try {
      if (editando) {
        await nominaApi.actualizarEmpleado(editando.id, datos);
        mostrarAviso("Empleado actualizado", { detalle: datos.nombre });
      } else {
        await nominaApi.crearEmpleado(datos);
        mostrarAviso("Empleado registrado", { detalle: datos.nombre });
      }
      setForm(null);
      refrescarAvisos();
      void cargar();
    } catch (err) {
      setErrorForm(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  async function cambiarEstado(e: Empleado, nuevo: "activo" | "inactivo", fecha: string | null) {
    try {
      await nominaApi.actualizarEmpleado(e.id, { ...payloadEmpleado(e), estado: nuevo, fecha_salida: fecha });
      mostrarAviso(nuevo === "inactivo" ? `${e.nombre} dado de baja` : `${e.nombre} reactivado`);
      setBaja(null);
      void cargar();
    } catch (err) {
      mostrarAviso("No se pudo cambiar el estado", { detalle: mensajeError(err), error: true });
    }
  }

  async function leerArchivo(archivo: File | undefined) {
    if (!archivo) return;
    setTextoCsv(await archivo.text());
    setResultado(null);
    setErrorImp(null);
  }

  async function importarCsv() {
    setErrorImp(null);
    try {
      const r = await nominaApi.importarEmpleados(textoCsv);
      setResultado(r);
      if (r.creados + r.actualizados > 0) {
        mostrarAviso("Empleados importados", { detalle: `${r.creados} nuevos · ${r.actualizados} actualizados` });
        refrescarAvisos();
        void cargar();
      }
    } catch (err) {
      setErrorImp(mensajeError(err));
    }
  }

  async function calcularPrestaciones(p: NonNullable<typeof prest>) {
    try {
      const r = await nominaApi.prestaciones(p.e.id, p.fecha, p.desahucio);
      setPrest({ ...p, r, error: null });
    } catch (err) {
      setPrest({ ...p, r: null, error: mensajeError(err) });
    }
  }

  return (
    <NominaShell>
      <Encabezado
        titulo="Empleados"
        sub={`${activos} empleado${activos === 1 ? "" : "s"} activo${activos === 1 ? "" : "s"}`}
        acciones={
          <>
            <button type="button" className="btn btn-o" onClick={() => { setImportar(true); setResultado(null); setErrorImp(null); setTextoCsv(""); }}>
              Importar CSV
            </button>
            <button type="button" className="btn btn-p" onClick={() => abrir()}>
              + Nuevo empleado
            </button>
          </>
        }
      />
      {error && <AvisoError titulo="No se pudo cargar la lista." mensaje={error} onReintentar={() => void cargar()} />}
      <section className="card">
        <div className="filters">
          <Buscador valor={q} onCambiar={setQ} placeholder="Buscar por nombre, cédula, cargo o departamento" />
          <select className="inp inp-auto" value={estado} onChange={(e) => setEstado(e.target.value as typeof estado)} aria-label="Estado">
            <option value="activo">Activos</option>
            <option value="inactivo">Dados de baja</option>
            <option value="">Todos</option>
          </select>
        </div>
        {empleados === null ? (
          <Cargando />
        ) : empleados.length === 0 && !error ? (
          <Vacio titulo="Aún no tienes empleados" texto="Agrega a tu equipo manualmente o importa un archivo CSV con cédula y salario.">
            <div className="row" style={{ justifyContent: "center" }}>
              <button type="button" className="btn btn-o" onClick={() => setImportar(true)}>Importar CSV</button>
              <button type="button" className="btn btn-p" onClick={() => abrir()}>+ Nuevo empleado</button>
            </div>
          </Vacio>
        ) : filas.length === 0 ? (
          <SinResultados q={q} onLimpiar={() => { setQ(""); setEstado(""); }} />
        ) : (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 760 }}>
              <thead>
                <tr>
                  <th>Empleado</th>
                  <th>Cargo</th>
                  <th>Pago</th>
                  <th className="num">Salario (RD$)</th>
                  <th>Estado</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((e) => (
                  <tr key={e.id}>
                    <td style={{ fontWeight: 500, color: "#111827" }}>
                      {e.nombre}
                      <span className="nm-sub mono">{formatoCedula(e.cedula)}</span>
                    </td>
                    <td className="mut">
                      {e.cargo ?? "—"}
                      {e.departamento && <span className="nm-sub">{e.departamento}</span>}
                    </td>
                    <td className="mut">
                      {e.tipo_pago === "quincenal" ? "Quincenal" : "Mensual"}
                      <span className="nm-sub">Desde {fechaLegible(e.fecha_ingreso)}</span>
                    </td>
                    <td className="num">{formatoMonto(e.salario_mensual)}</td>
                    <td>
                      {e.estado === "activo" ? <span className="bd b-acep">Activo</span> : <span className="bd b-borr">Baja {e.fecha_salida ? fechaLegible(e.fecha_salida) : ""}</span>}
                    </td>
                    <td>
                      <div className="fila-acc">
                        <button type="button" className="btn btn-g btn-sm" onClick={() => abrir(e)}>Editar</button>
                        <button type="button" className="btn btn-g btn-sm" onClick={() => void calcularPrestaciones({ e, fecha: e.fecha_salida ?? hoyIso(), desahucio: true, r: null, error: null })}>
                          Prestaciones
                        </button>
                        {e.estado === "activo" ? (
                          <button type="button" className="btn btn-g btn-sm" style={{ color: "#B91C1C" }} onClick={() => { setBaja(e); setFechaBaja(hoyIso()); }}>
                            Dar de baja
                          </button>
                        ) : (
                          <button type="button" className="btn btn-g btn-sm" onClick={() => void cambiarEstado(e, "activo", null)}>Reactivar</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {form && (
        <Modal
          titulo={editando ? `Editar · ${editando.nombre}` : "Nuevo empleado"}
          ancho={760}
          onCerrar={() => setForm(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setForm(null)}>Cancelar</button>
              <button type="button" className="btn btn-p" onClick={() => void guardar()} disabled={guardando}>
                {guardando ? "Guardando…" : "Guardar empleado"}
              </button>
            </>
          }
        >
          <div className="form-grid">
            <div className="fld fld-full">
              <label className="lbl" htmlFor="en">Nombre completo</label>
              <input id="en" className="inp" value={form.nombre} onChange={(e) => campo("nombre", e.target.value)} autoFocus />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ec">Cédula</label>
              <input id="ec" className="inp mono" value={form.cedula} placeholder="402-2345678-1" onChange={(e) => campo("cedula", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="es">Salario mensual (RD$)</label>
              <input id="es" type="number" min="0" step="0.01" className="inp" value={form.salario_mensual} onChange={(e) => campo("salario_mensual", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ecg">Cargo</label>
              <input id="ecg" className="inp" value={form.cargo ?? ""} onChange={(e) => campo("cargo", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ed">Departamento</label>
              <input id="ed" className="inp" value={form.departamento ?? ""} onChange={(e) => campo("departamento", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ef">Fecha de ingreso</label>
              <input id="ef" type="date" className="inp" value={form.fecha_ingreso} onChange={(e) => campo("fecha_ingreso", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="et">Tipo de pago</label>
              <select id="et" className="inp" value={form.tipo_pago} onChange={(e) => campo("tipo_pago", e.target.value as EmpleadoPayload["tipo_pago"])}>
                <option value="mensual">Mensual</option>
                <option value="quincenal">Quincenal</option>
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="eb">Banco</label>
              <input id="eb" className="inp" list="nm-bancos" value={form.banco ?? ""} onChange={(e) => campo("banco", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ecu">Cuenta bancaria</label>
              <input id="ecu" className="inp mono" value={form.cuenta_bancaria ?? ""} onChange={(e) => campo("cuenta_bancaria", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="etc">Tipo de cuenta</label>
              <select id="etc" className="inp" value={form.tipo_cuenta ?? "ahorro"} onChange={(e) => campo("tipo_cuenta", e.target.value as EmpleadoPayload["tipo_cuenta"])}>
                <option value="ahorro">Ahorro</option>
                <option value="corriente">Corriente</option>
              </select>
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="eafp">AFP</label>
              <input id="eafp" className="inp" list="nm-afps" value={form.afp ?? ""} onChange={(e) => campo("afp", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ears">ARS</label>
              <input id="ears" className="inp" list="nm-arss" value={form.ars ?? ""} onChange={(e) => campo("ars", e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="ecorreo">Correo (recibos)</label>
              <input id="ecorreo" type="email" className="inp" value={form.correo ?? ""} onChange={(e) => campo("correo", e.target.value)} />
            </div>
          </div>
          <datalist id="nm-bancos">{BANCOS.map((b) => <option key={b} value={b} />)}</datalist>
          <datalist id="nm-afps">{AFPS.map((b) => <option key={b} value={b} />)}</datalist>
          <datalist id="nm-arss">{ARSS.map((b) => <option key={b} value={b} />)}</datalist>
          {editando && <p className="hint" style={{ margin: 0 }}>Vacaciones que le corresponden hoy: {editando.dias_vacaciones} días laborables (Código de Trabajo, arts. 177 y 180).</p>}
          {errorForm && <AvisoError mensaje={errorForm} />}
        </Modal>
      )}

      {baja && (
        <Modal
          titulo={`Dar de baja a ${baja.nombre}`}
          onCerrar={() => setBaja(null)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => setBaja(null)}>Cancelar</button>
              <button type="button" className="btn btn-d" onClick={() => void cambiarEstado(baja, "inactivo", fechaBaja)}>Dar de baja</button>
            </>
          }
        >
          <p style={{ margin: 0 }}>Deja de entrar en las nóminas que empiecen después de la fecha de salida. Su historial se conserva.</p>
          <div className="fld">
            <label className="lbl" htmlFor="fbaja">Fecha de salida</label>
            <input id="fbaja" type="date" className="inp" value={fechaBaja} onChange={(e) => setFechaBaja(e.target.value)} />
          </div>
        </Modal>
      )}

      {importar && (
        <Modal
          titulo="Importar empleados desde CSV"
          ancho={720}
          onCerrar={() => setImportar(false)}
          pie={
            <>
              <button type="button" className="btn btn-o" onClick={() => descargarCsv("plantilla_empleados.csv", PLANTILLA)}>Descargar plantilla</button>
              <button type="button" className="btn btn-p" onClick={() => void importarCsv()} disabled={!textoCsv.trim()}>Importar</button>
            </>
          }
        >
          <div className="nm-import stack-sm">
            <p style={{ margin: 0 }}>
              Columnas: <b>cédula, nombre, salario y fecha de ingreso</b> (obligatorias); cargo, departamento, tipo de pago, banco, cuenta, tipo de cuenta,
              AFP, ARS y correo son opcionales. Separado por comas o punto y coma; fechas AAAA-MM-DD o DD/MM/AAAA. Si la cédula ya existe, se actualiza.
            </p>
            <input type="file" accept=".csv,text/csv,text/plain" aria-label="Archivo CSV" onChange={(e) => void leerArchivo(e.target.files?.[0])} />
            <textarea className="inp mono" value={textoCsv} onChange={(e) => setTextoCsv(e.target.value)} aria-label="Contenido CSV" placeholder="O pegue aquí el contenido…" />
            {errorImp && <AvisoError mensaje={errorImp} />}
            {resultado && (
              <div className={resultado.errores.length ? "al al-w" : "al al-s"} role="status">
                <span>
                  <b>{resultado.creados} nuevos · {resultado.actualizados} actualizados</b>
                  {resultado.errores.length > 0 && (
                    <>
                      {" "}· {resultado.errores.length} fila{resultado.errores.length === 1 ? "" : "s"} con error:
                      <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                        {resultado.errores.map((e) => (
                          <li key={e.fila}>Fila {e.fila}: {e.mensaje}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </span>
              </div>
            )}
          </div>
        </Modal>
      )}

      {prest && (
        <Modal
          titulo={`Prestaciones laborales · ${prest.e.nombre}`}
          ancho={620}
          onCerrar={() => setPrest(null)}
          pie={<button type="button" className="btn btn-p" onClick={() => setPrest(null)}>Cerrar</button>}
        >
          <div className="form-grid">
            <div className="fld">
              <label className="lbl" htmlFor="pfs">Fecha de salida</label>
              <input id="pfs" type="date" className="inp" value={prest.fecha} onChange={(e) => void calcularPrestaciones({ ...prest, fecha: e.target.value })} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pmot">Motivo</label>
              <select id="pmot" className="inp" value={prest.desahucio ? "d" : "r"} onChange={(e) => void calcularPrestaciones({ ...prest, desahucio: e.target.value === "d" })}>
                <option value="d">Desahucio por el empleador</option>
                <option value="r">Renuncia</option>
              </select>
            </div>
          </div>
          {prest.error && <AvisoError mensaje={prest.error} />}
          {prest.r && (
            <div className="nm-det">
              <span>Tiempo de servicio</span><span className="num">{prest.r.meses_servicio} meses</span>
              <span>Salario diario (÷ 23.83)</span><span className="num">RD$ {formatoMonto(prest.r.salario_diario)}</span>
              <span>Preaviso · {prest.r.dias_preaviso} días (art. 76)</span><span className="num">{formatoMonto(prest.r.preaviso)}</span>
              <span>Cesantía · {Number(prest.r.dias_cesantia)} días (art. 80)</span><span className="num">{formatoMonto(prest.r.cesantia)}</span>
              <span>Vacaciones · {prest.r.dias_vacaciones} días (art. 177)</span><span className="num">{formatoMonto(prest.r.vacaciones)}</span>
              <span>Regalía proporcional (art. 219)</span><span className="num">{formatoMonto(prest.r.regalia)}</span>
              <span className="t">Total estimado</span><span className="t num">RD$ {formatoMonto(prest.r.total)}</span>
            </div>
          )}
          <p className="hint" style={{ margin: 0 }}>
            Estimado con el salario actual. No incluye vacaciones ya disfrutadas ni salarios pendientes; verifíquelo con su asesor laboral.
          </p>
        </Modal>
      )}
      {toast}
    </NominaShell>
  );
}
