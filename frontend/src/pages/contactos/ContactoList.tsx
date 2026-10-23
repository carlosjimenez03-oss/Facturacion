import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Toast } from "../../components/detalle/Toast";
import type { Aviso } from "../../components/detalle/Toast";
import { IconAlerta, IconMas } from "../../layout/icons";
import { erpApi, ErpApiError } from "../../lib/erpApi";
import type { Cliente, TipoContacto } from "../../lib/erpApi";
import { formatoMonto, formatoRnc } from "../../lib/marca";
import { correoValido, errorIdentificacion, mensajeErp, payloadDesde, TIPO_CONTACTO_LABEL, tipoPorDigitos } from "./contactos";
import { ModalEliminar } from "./ModalEliminar";

type Pestana = "Todos" | "Clientes" | "Proveedores";

const PESTANAS: { id: Pestana; test: (c: Cliente) => boolean }[] = [
  { id: "Todos", test: () => true },
  { id: "Clientes", test: (c) => c.tipo !== "proveedor" },
  { id: "Proveedores", test: (c) => c.tipo !== "cliente" },
];

/** Resultado del botón "Verificar" del alta rápida. */
type Verificacion =
  | { tipo: "invalido"; mensaje: string }
  | { tipo: "existe"; cliente: Cliente }
  | { tipo: "libre"; mensaje: string }
  | { tipo: "sin-conexion"; mensaje: string };

export function ContactoList() {
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pestana, setPestana] = useState<Pestana>("Todos");
  const [q, setQ] = useState("");
  const [consulta, setConsulta] = useState("");
  const [creando, setCreando] = useState(false);
  const [aEliminar, setAEliminar] = useState<{ cliente: Cliente; bloqueo?: string } | null>(null);
  const [eliminando, setEliminando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(null);
  const cerrarAviso = useCallback(() => setAviso(null), []);

  // La búsqueda va al servidor (nombre o RNC), con una pequeña espera mientras se escribe.
  useEffect(() => {
    const t = window.setTimeout(() => setConsulta(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const cargar = useCallback(() => {
    setCargando(true);
    erpApi
      .listarClientes(consulta ? { q: consulta.replace(/-/g, "") } : undefined)
      .then((lista) => {
        setClientes(lista);
        setError(null);
      })
      .catch((err) => setError(mensajeErp(err)))
      .finally(() => setCargando(false));
  }, [consulta]);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const filas = useMemo(() => {
    const test = PESTANAS.find((p) => p.id === pestana)!.test;
    return clientes.filter(test).sort((a, b) => a.nombre.localeCompare(b.nombre, "es"));
  }, [clientes, pestana]);

  async function eliminar() {
    if (!aEliminar) return;
    setEliminando(true);
    try {
      await erpApi.eliminarCliente(aEliminar.cliente.id);
      setAEliminar(null);
      setAviso({ texto: "Contacto eliminado" });
      cargar();
    } catch (err) {
      if (err instanceof ErpApiError && err.status === 409) {
        setAEliminar({ cliente: aEliminar.cliente, bloqueo: err.message });
      } else {
        setAEliminar(null);
        setAviso({ texto: "No se pudo eliminar el contacto", detalle: mensajeErp(err), tono: "error" });
      }
    } finally {
      setEliminando(false);
    }
  }

  async function desactivar() {
    if (!aEliminar) return;
    setEliminando(true);
    try {
      await erpApi.actualizarCliente(aEliminar.cliente.id, payloadDesde(aEliminar.cliente, { activo: false }));
      setAEliminar(null);
      setAviso({ texto: "Contacto desactivado", detalle: "Ya no aparecerá al facturar; su historial se conserva." });
      cargar();
    } catch (err) {
      setAEliminar(null);
      setAviso({ texto: "No se pudo desactivar el contacto", detalle: mensajeErp(err), tono: "error" });
    } finally {
      setEliminando(false);
    }
  }

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>Contactos</h1>
          <p>Clientes y proveedores con su RNC o cédula, condiciones de pago y saldo pendiente</p>
        </div>
        <div className="row">
          <span className="tip" data-tip="La importación desde Excel estará disponible próximamente.">
            <button type="button" className="btn btn-o" disabled>
              Importar desde Excel
            </button>
          </span>
          <button type="button" className="btn btn-p" onClick={() => setCreando(!creando)} aria-expanded={creando}>
            {creando ? (
              "Cerrar formulario"
            ) : (
              <>
                <IconMas /> Nuevo contacto
              </>
            )}
          </button>
        </div>
      </div>

      {creando && (
        <NuevoContacto
          onCancelar={() => setCreando(false)}
          onGuardado={(c) => {
            setCreando(false);
            setAviso({ texto: "Contacto guardado", detalle: c.nombre });
            cargar();
          }}
        />
      )}

      <section className="card">
        <div className="tabs" role="tablist" aria-label="Tipo de contacto">
          {PESTANAS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={pestana === p.id}
              className={pestana === p.id ? "tab on" : "tab"}
              onClick={() => setPestana(p.id)}
            >
              {p.id} <span className="chip chip-sm">{clientes.filter(p.test).length}</span>
            </button>
          ))}
        </div>
        <div className="filters">
          <div className="tb-search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input className="inp" type="search" placeholder="Buscar por nombre o RNC" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar contactos" />
          </div>
        </div>

        {error && (
          <div className="pad">
            <div className="al al-d" role="alert">
              <IconAlerta />
              <span style={{ flex: 1 }}>
                <b>No se pudieron cargar los contactos.</b> {error}
              </span>
              <button type="button" className="btn btn-o btn-sm" onClick={cargar}>
                Reintentar
              </button>
            </div>
          </div>
        )}
        {cargando && clientes.length === 0 && !error && <p className="pad mut">Cargando…</p>}
        {!cargando && !error && filas.length === 0 && (
          <div className="empty">
            <span className="ei">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" aria-hidden="true">
                <circle cx="9" cy="7" r="4" />
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              </svg>
            </span>
            <h3>No encontramos contactos</h3>
            <p>{consulta || pestana !== "Todos" ? "Prueba con otro nombre o RNC, o crea un contacto nuevo." : "Todavía no tiene contactos. Cree el primero o se agregarán solos al facturar."}</p>
            <button type="button" className="btn btn-p btn-sm" onClick={() => setCreando(true)}>
              Nuevo contacto
            </button>
          </div>
        )}
        {filas.length > 0 && (
          <div className="tw">
            <table className="tbl" style={{ minWidth: 900, opacity: cargando ? 0.6 : 1 }}>
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>Tipo</th>
                  <th>RNC / Cédula</th>
                  <th>
                    <span className="tip" data-tip="La consulta del RNC o cédula en el padrón de la DGII aún no está disponible en Kontara.">
                      Estado DGII ⓘ
                    </span>
                  </th>
                  <th>Correo</th>
                  <th className="num">Saldo (RD$)</th>
                  <th style={{ textAlign: "right" }}>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {filas.map((c) => {
                  const saldo = Number(c.saldo) || 0;
                  return (
                    <tr key={c.id}>
                      <td>
                        <Link to={`/contactos/${c.id}`} className="link" style={{ fontWeight: 600, color: "#111827" }}>
                          {c.nombre}
                        </Link>
                        {!c.activo && (
                          <span className="bd b-borr nodot" style={{ marginLeft: 8, height: 20, fontSize: 11 }}>
                            Inactivo
                          </span>
                        )}
                      </td>
                      <td>
                        <span className="chip">{TIPO_CONTACTO_LABEL[c.tipo] ?? c.tipo}</span>
                      </td>
                      <td className="mono mut nowrap">{c.rnc ? formatoRnc(c.rnc) : "—"}</td>
                      <td>
                        <span className="bd b-borr nodot">Sin verificar</span>
                      </td>
                      <td className="mut">{c.correo || "—"}</td>
                      <td className="num" style={{ fontWeight: 600 }}>
                        {formatoMonto(saldo)}
                        {saldo > 0 && <div className="hint" style={{ fontWeight: 500 }}>Por cobrar</div>}
                      </td>
                      <td>
                        <div style={{ display: "flex", justifyContent: "flex-end", gap: 2, whiteSpace: "nowrap" }}>
                          {c.tipo === "proveedor" ? (
                            <Link to="/inventario/compras/nueva" className="btn btn-g btn-sm">
                              Registrar compra
                            </Link>
                          ) : (
                            <Link to={`/facturas/nueva?cliente=${c.id}`} className="btn btn-g btn-sm">
                              Facturar
                            </Link>
                          )}
                          <button type="button" className="btn btn-g btn-sm" onClick={() => setAEliminar({ cliente: c })} style={{ color: "#B91C1C" }}>
                            Eliminar
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

      {aEliminar && (
        <ModalEliminar
          nombre={aEliminar.cliente.nombre}
          bloqueo={aEliminar.bloqueo}
          procesando={eliminando}
          onCancelar={() => setAEliminar(null)}
          onEliminar={eliminar}
          onDesactivar={desactivar}
        />
      )}
      <Toast aviso={aviso} onCerrar={cerrarAviso} />
    </div>
  );
}

/** Alta rápida del diseño: tipo, RNC o cédula, nombre, correo y teléfono. */
function NuevoContacto({ onCancelar, onGuardado }: { onCancelar: () => void; onGuardado: (c: Cliente) => void }) {
  const [tipo, setTipo] = useState<TipoContacto>("cliente");
  const [rnc, setRnc] = useState("");
  const [nombre, setNombre] = useState("");
  const [correo, setCorreo] = useState("");
  const [telefono, setTelefono] = useState("");
  const [verificacion, setVerificacion] = useState<Verificacion | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const errorId = errorIdentificacion(tipoPorDigitos(rnc) ?? "", rnc);
  const puedeGuardar = Boolean(rnc.trim() && !errorId && nombre.trim()) && verificacion?.tipo !== "existe" && !guardando;

  async function verificar() {
    const digitos = rnc.replace(/\D/g, "");
    const tipoId = tipoPorDigitos(rnc);
    if (!tipoId) return setVerificacion({ tipo: "invalido", mensaje: "El RNC debe tener 9 dígitos y la cédula 11 dígitos." });
    try {
      const existente = await erpApi.clientePorRnc(digitos);
      setVerificacion({ tipo: "existe", cliente: existente });
    } catch (err) {
      if (err instanceof ErpApiError && err.status === 404) {
        setVerificacion({
          tipo: "libre",
          mensaje: `${tipoId === "rnc" ? "RNC" : "Cédula"} con formato válido y sin registrar en sus contactos. La consulta al padrón de la DGII aún no está disponible: confirme el nombre con el contacto.`,
        });
      } else {
        setVerificacion({ tipo: "sin-conexion", mensaje: mensajeErp(err) });
      }
    }
  }

  async function guardar() {
    setError(null);
    if (!correoValido(correo)) return setError("Revise el correo: debe tener el formato nombre@empresa.do.");
    setGuardando(true);
    try {
      const creado = await erpApi.crearCliente({
        tipo,
        tipo_identificacion: tipoPorDigitos(rnc),
        rnc: rnc.replace(/\D/g, ""),
        nombre: nombre.trim(),
        correo: correo.trim() || null,
        telefono: telefono.trim() || null,
      });
      onGuardado(creado);
    } catch (err) {
      setError(mensajeErp(err));
    } finally {
      setGuardando(false);
    }
  }

  const claseVerificacion =
    verificacion?.tipo === "invalido" ? "al al-d" : verificacion?.tipo === "libre" ? "al al-i" : "al al-w";

  return (
    <section className="card">
      <div className="card-h">
        <h2>Nuevo contacto</h2>
        <div className="seg" role="group" aria-label="Tipo de contacto">
          <button type="button" className={tipo === "cliente" ? "on" : ""} onClick={() => setTipo("cliente")} aria-pressed={tipo === "cliente"}>
            Cliente
          </button>
          <button type="button" className={tipo === "proveedor" ? "on" : ""} onClick={() => setTipo("proveedor")} aria-pressed={tipo === "proveedor"}>
            Proveedor
          </button>
        </div>
      </div>
      <div className="card-b" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, alignItems: "end" }}>
        <div className="fld">
          <label className="lbl" htmlFor="nr">
            RNC o cédula <span className="req">*</span>
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              id="nr"
              className="inp mono"
              value={rnc}
              onChange={(e) => {
                setRnc(e.target.value.replace(/[^\d-]/g, ""));
                setVerificacion(null);
              }}
              placeholder="9 u 11 dígitos"
              inputMode="numeric"
            />
            <button type="button" className="btn btn-o" onClick={verificar} disabled={!rnc.trim()}>
              Verificar
            </button>
          </div>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="nn">
            Nombre o razón social <span className="req">*</span>
          </label>
          <input id="nn" className="inp" value={nombre} onChange={(e) => setNombre(e.target.value)} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="ne">Correo</label>
          <input id="ne" type="email" className="inp" value={correo} onChange={(e) => setCorreo(e.target.value)} placeholder="correo@empresa.do" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="nt">Teléfono / WhatsApp</label>
          <input id="nt" type="tel" className="inp" value={telefono} onChange={(e) => setTelefono(e.target.value)} placeholder="809-555-0000" />
        </div>
        {verificacion && (
          <div className={claseVerificacion} style={{ gridColumn: "1 / -1" }}>
            {verificacion.tipo === "existe" ? (
              <span>
                Ya existe un contacto con este {verificacion.cliente.rnc?.length === 11 ? "número de cédula" : "RNC"}:{" "}
                <b>{verificacion.cliente.nombre}</b>. <Link to={`/contactos/${verificacion.cliente.id}`}>Ver contacto</Link>
              </span>
            ) : (
              <span>{verificacion.mensaje}</span>
            )}
          </div>
        )}
        {rnc.trim() && errorId && !verificacion && <span className="err-t" style={{ gridColumn: "1 / -1" }}>{errorId}</span>}
        {error && (
          <div className="al al-d" role="alert" style={{ gridColumn: "1 / -1" }}>
            <IconAlerta />
            <span>{error}</span>
          </div>
        )}
        <div className="row" style={{ gridColumn: "1 / -1", justifyContent: "space-between" }}>
          <Link to="/contactos/nuevo" className="btn btn-g btn-sm">
            Más datos: dirección, crédito y notas
          </Link>
          <div className="row">
            <button type="button" className="btn btn-o" onClick={onCancelar}>
              Cancelar
            </button>
            <button type="button" className="btn btn-p" onClick={guardar} disabled={!puedeGuardar}>
              {guardando ? "Guardando…" : "Guardar contacto"}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
