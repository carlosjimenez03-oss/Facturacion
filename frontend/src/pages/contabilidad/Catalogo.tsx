// Catálogo de cuentas (pestaña de Contabilidad.dc.html): árbol por código que se expande y contrae, con el
// saldo de cada cuenta; alta y edición de cuentas, y las cuentas por defecto de los asientos automáticos.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode, RefObject } from "react";
import { useLocation } from "react-router-dom";
import { IconAlerta } from "../../layout/icons";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { CuentaContable, CuentaContableActualizar, CuentaPorDefecto, GrupoConcepto, Naturaleza, TipoCuentaContable } from "../../lib/contabilidadApi";
import { AvisoError, Buscador, Cargando, Modal, Vacio } from "../ingresos/IngresosShell";
import { mensajeError } from "../ingresos/ingresosUtil";
import { montoContable } from "../impuestos/impuestosUtil";
import { Interruptor } from "../inventario/InventarioShell";
import type { MostrarAviso } from "./Contabilidad";
import { etiquetaCuentaContable, GRUPO_CONCEPTO, TIPO_CUENTA_CONTABLE } from "./contabilidadUtil";

function Chevron({ abierto }: { abierto: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true" style={{ transform: abierto ? "rotate(90deg)" : "none" }}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

const NATURALEZA_DE_TIPO: Record<TipoCuentaContable, Naturaleza> = {
  activo: "deudora",
  costo: "deudora",
  gasto: "deudora",
  pasivo: "acreedora",
  patrimonio: "acreedora",
  ingreso: "acreedora",
};

export function CatalogoTab({ mostrarAviso }: { mostrarAviso: MostrarAviso }) {
  const [cuentas, setCuentas] = useState<CuentaContable[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [abiertas, setAbiertas] = useState<Set<string> | null>(null);
  const [q, setQ] = useState("");
  const [form, setForm] = useState<{ cuenta: CuentaContable | null; codigo?: string } | null>(null);
  const formRef = useRef<HTMLElement>(null);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const lista = await contabilidadApi.listarCuentas();
      setCuentas(lista);
      setError(null);
      // Como el prototipo: el primer grupo abierto.
      setAbiertas((prev) => prev ?? new Set(lista.filter((c) => c.nivel === 1).slice(0, 1).map((c) => c.id)));
    } catch (err) {
      setCuentas([]);
      setError(mensajeError(err));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const porId = useMemo(() => new Map(cuentas.map((c) => [c.id, c])), [cuentas]);
  const texto = q.trim().toLowerCase();

  const visibles = useMemo(() => {
    if (texto) {
      // Coincidencias y sus ancestros, todo abierto.
      const ids = new Set<string>();
      for (const c of cuentas) {
        if (!`${c.codigo} ${c.nombre}`.toLowerCase().includes(texto)) continue;
        let x: CuentaContable | undefined = c;
        while (x) {
          ids.add(x.id);
          x = x.padre_id ? porId.get(x.padre_id) : undefined;
        }
      }
      return cuentas.filter((c) => ids.has(c.id));
    }
    const abiertasSet = abiertas ?? new Set<string>();
    return cuentas.filter((c) => {
      let p = c.padre_id ? porId.get(c.padre_id) : undefined;
      while (p) {
        if (!abiertasSet.has(p.id)) return false;
        p = p.padre_id ? porId.get(p.padre_id) : undefined;
      }
      return true;
    });
  }, [cuentas, porId, abiertas, texto]);

  const grupos = cuentas.filter((c) => c.hijos > 0);
  const todoAbierto = grupos.length > 0 && grupos.every((c) => abiertas?.has(c.id));

  function alternar(id: string) {
    setAbiertas((prev) => {
      const n = new Set(prev ?? []);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  function abrirForm(cuenta: CuentaContable | null, codigo?: string) {
    setForm({ cuenta, codigo });
    window.setTimeout(() => formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  function guardada(c: CuentaContable, nueva: boolean) {
    setForm(null);
    mostrarAviso(nueva ? "Cuenta creada" : "Cuenta actualizada", { detalle: etiquetaCuentaContable(c) });
    if (c.padre_id) setAbiertas((prev) => new Set([...(prev ?? []), c.padre_id!]));
    void cargar();
  }

  return (
    <>
      {form && (
        <CuentaContableForm
          key={form.cuenta?.id ?? `nueva-${form.codigo ?? ""}`}
          refSeccion={formRef}
          cuenta={form.cuenta}
          codigoInicial={form.codigo}
          cuentas={cuentas}
          onGuardada={guardada}
          onEliminada={(c) => {
            setForm(null);
            mostrarAviso("Cuenta eliminada", { detalle: etiquetaCuentaContable(c) });
            void cargar();
          }}
          onCancelar={() => setForm(null)}
        />
      )}

      {error && <AvisoError titulo="No se pudo cargar el catálogo de cuentas." mensaje={error} onReintentar={() => void cargar()} />}

      <section className="card">
        <div className="card-h">
          <div>
            <h2>Catálogo de cuentas</h2>
            {cuentas.length > 0 && <span className="hint">{cuentas.length} cuentas · saldos a hoy, solo asientos contabilizados</span>}
          </div>
          <div className="row">
            <button type="button" className="btn btn-o btn-sm" onClick={() => setAbiertas(todoAbierto ? new Set() : new Set(grupos.map((c) => c.id)))} disabled={grupos.length === 0 || !!texto}>
              {todoAbierto ? "Contraer todo" : "Expandir todo"}
            </button>
            <button type="button" className="btn btn-p btn-sm" onClick={() => abrirForm(null)}>
              + Nueva cuenta
            </button>
          </div>
        </div>
        {cuentas.length > 0 && (
          <div className="filters">
            <Buscador valor={q} onCambiar={setQ} placeholder="Buscar por código o nombre" />
          </div>
        )}
        {cargando && cuentas.length === 0 && <Cargando />}
        {!cargando && !error && cuentas.length === 0 && (
          <Vacio titulo="El catálogo está vacío" texto="El catálogo sugerido se siembra al iniciar el servicio cuando no hay cuentas. Puede crear las suyas desde aquí." />
        )}
        {cuentas.length > 0 && visibles.length === 0 && <Vacio titulo="Sin resultados" texto={`No hay cuentas que coincidan con «${q}».`} />}
        <div role="tree" aria-label="Catálogo de cuentas">
          {visibles.map((c) => {
            const abierta = texto ? true : !!abiertas?.has(c.id);
            const grupo = c.hijos > 0;
            const peso = c.nivel <= 1 ? 650 : grupo ? 600 : 400;
            return (
              <div
                key={c.id}
                role="treeitem"
                aria-level={c.nivel}
                aria-expanded={grupo ? abierta : undefined}
                className={c.activa ? "ct-arbol-f" : "ct-arbol-f off"}
                style={{ paddingLeft: 20 + (c.nivel - 1) * 28 }}
              >
                {grupo ? (
                  <button type="button" className="ib" onClick={() => alternar(c.id)} aria-label={`${abierta ? "Contraer" : "Expandir"} ${c.nombre}`} disabled={!!texto}>
                    <Chevron abierto={abierta} />
                  </button>
                ) : (
                  <span className="ct-hoja" />
                )}
                <span className="mono ct-cod">{c.codigo}</span>
                <span className="ct-nom" style={{ fontWeight: peso }}>
                  {c.nombre}
                  {!c.activa && <span className="chip chip-sm" style={{ marginLeft: 8 }}>Inactiva</span>}
                </span>
                <span className="ct-acc row" style={{ gap: 2 }}>
                  {(grupo || !c.tiene_movimientos) && (
                    <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(null, `${c.codigo}.`)} title={`Nueva subcuenta de ${c.nombre}`}>
                      + Subcuenta
                    </button>
                  )}
                  <button type="button" className="btn btn-g btn-sm" onClick={() => abrirForm(c)}>
                    Editar
                  </button>
                </span>
                <span className="num ct-sal" style={{ fontWeight: peso }}>
                  {montoContable(c.saldo)}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <CuentasPorDefecto cuentas={cuentas} mostrarAviso={mostrarAviso} />
    </>
  );
}

function CuentaContableForm({
  refSeccion,
  cuenta,
  codigoInicial,
  cuentas,
  onGuardada,
  onEliminada,
  onCancelar,
}: {
  refSeccion: RefObject<HTMLElement>;
  cuenta: CuentaContable | null;
  codigoInicial?: string;
  cuentas: CuentaContable[];
  onGuardada: (c: CuentaContable, nueva: boolean) => void;
  onEliminada: (c: CuentaContable) => void;
  onCancelar: () => void;
}) {
  const [codigo, setCodigo] = useState(cuenta?.codigo ?? codigoInicial ?? "");
  const [nombre, setNombre] = useState(cuenta?.nombre ?? "");
  const [tipo, setTipo] = useState<TipoCuentaContable>(cuenta?.tipo ?? "gasto");
  const [naturaleza, setNaturaleza] = useState<Naturaleza | "">(cuenta?.naturaleza ?? "");
  const [acepta, setAcepta] = useState(cuenta?.acepta_movimientos ?? true);
  const [activa, setActiva] = useState(cuenta?.activa ?? true);
  const [descripcion, setDescripcion] = useState(cuenta?.descripcion ?? "");
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [eliminar, setEliminar] = useState(false);

  // El padre sale del código: "6.1.04.02" es hija de "6.1.04".
  const codigoPadre = codigo.trim().includes(".") ? codigo.trim().replace(/\.[^.]*$/, "") : "";
  const padre = cuentas.find((c) => c.codigo === codigoPadre);
  const raiz = !codigoPadre;
  const tipoEfectivo = cuenta?.tipo ?? (raiz ? tipo : padre?.tipo);
  const naturalezaDefecto = padre?.naturaleza ?? (tipoEfectivo ? NATURALEZA_DE_TIPO[tipoEfectivo] : "deudora");

  async function guardar() {
    const cod = codigo.trim();
    if (!/^\d+(\.\d+)*$/.test(cod)) return setError("El código debe tener números separados por puntos, por ejemplo 6.1.04.02.");
    if (!nombre.trim()) return setError("Indique el nombre de la cuenta.");
    if (!cuenta && !raiz && !padre) return setError(`No existe la cuenta ${codigoPadre}: cree primero la cuenta de grupo.`);
    setGuardando(true);
    setError(null);
    try {
      if (!cuenta) {
        onGuardada(
          await contabilidadApi.crearCuenta({
            codigo: cod,
            nombre: nombre.trim(),
            tipo: raiz ? tipo : null,
            naturaleza: naturaleza || null,
            acepta_movimientos: acepta,
            activa,
            descripcion: descripcion.trim() || null,
          }),
          true,
        );
      } else {
        const cambios: CuentaContableActualizar = {};
        if (cod !== cuenta.codigo) cambios.codigo = cod;
        if (nombre.trim() !== cuenta.nombre) cambios.nombre = nombre.trim();
        if (naturaleza && naturaleza !== cuenta.naturaleza) cambios.naturaleza = naturaleza;
        if (acepta !== cuenta.acepta_movimientos) cambios.acepta_movimientos = acepta;
        if (activa !== cuenta.activa) cambios.activa = activa;
        if ((descripcion.trim() || null) !== cuenta.descripcion) cambios.descripcion = descripcion.trim() || null;
        onGuardada(Object.keys(cambios).length ? await contabilidadApi.actualizarCuenta(cuenta.id, cambios) : cuenta, false);
      }
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  async function confirmarEliminar() {
    if (!cuenta) return;
    try {
      await contabilidadApi.eliminarCuenta(cuenta.id);
      onEliminada(cuenta);
    } catch (err) {
      setEliminar(false);
      setError(mensajeError(err));
    }
  }

  return (
    <section className="card" ref={refSeccion}>
      <div className="card-h">
        <h2>{cuenta ? `Editar cuenta ${cuenta.codigo}` : "Nueva cuenta contable"}</h2>
        <span className="hint">{padre ? `Subcuenta de ${etiquetaCuentaContable(padre)}` : raiz && codigo.trim() ? "Cuenta raíz (grupo principal)" : "El padre sale del código"}</span>
      </div>
      <div className="card-b form-grid">
        <div className="fld">
          <label className="lbl" htmlFor="cc-cod">
            Código <span className="req">*</span>
          </label>
          <input id="cc-cod" className="inp mono" value={codigo} onChange={(e) => setCodigo(e.target.value)} placeholder="6.1.04.02" disabled={!!cuenta && cuenta.hijos > 0} />
          {cuenta && cuenta.hijos > 0 && <span className="hint">Una cuenta con subcuentas no cambia de código.</span>}
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cc-nom">
            Nombre <span className="req">*</span>
          </label>
          <input id="cc-nom" className="inp" value={nombre} onChange={(e) => setNombre(e.target.value)} placeholder="Combustible y lubricantes" />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cc-tipo">Tipo</label>
          <select id="cc-tipo" className="inp" value={tipoEfectivo ?? tipo} onChange={(e) => setTipo(e.target.value as TipoCuentaContable)} disabled={!!cuenta || !raiz}>
            {(Object.keys(TIPO_CUENTA_CONTABLE) as TipoCuentaContable[]).map((t) => (
              <option key={t} value={t}>
                {TIPO_CUENTA_CONTABLE[t]}
              </option>
            ))}
          </select>
          {!raiz && <span className="hint">Se hereda de la cuenta de grupo.</span>}
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="cc-nat">Naturaleza</label>
          <select id="cc-nat" className="inp" value={naturaleza} onChange={(e) => setNaturaleza(e.target.value as Naturaleza | "")}>
            {!cuenta && <option value="">Según el tipo ({naturalezaDefecto})</option>}
            <option value="deudora">Deudora (aumenta con débitos)</option>
            <option value="acreedora">Acreedora (aumenta con créditos)</option>
          </select>
          <span className="hint">Las cuentas «contra» (depreciación acumulada, devoluciones) llevan la contraria.</span>
        </div>
        <div className="fld fld-full">
          <label className="lbl" htmlFor="cc-desc">Descripción</label>
          <input id="cc-desc" className="inp" value={descripcion} onChange={(e) => setDescripcion(e.target.value)} placeholder="Opcional" />
        </div>
        <div className="fld">
          <Interruptor
            id="cc-mov"
            etiqueta="Acepta movimientos"
            ayuda="Solo las cuentas de detalle llevan líneas de asiento; las de grupo suman a sus subcuentas."
            valor={acepta}
            onCambiar={setAcepta}
            disabled={!!cuenta && cuenta.hijos > 0}
          />
        </div>
        <div className="fld">
          <Interruptor id="cc-act" etiqueta="Activa" ayuda="Las inactivas no se ofrecen en los asientos." valor={activa} onCambiar={setActiva} />
        </div>
        {padre?.acepta_movimientos && !cuenta && (
          <div className="al al-w fld-full">
            <IconAlerta />
            <span>
              {etiquetaCuentaContable(padre)} pasará a ser una cuenta de grupo. No se puede si ya tiene movimientos o es una cuenta por defecto o de un banco.
            </span>
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
          {cuenta && !cuenta.sistema && cuenta.hijos === 0 && !cuenta.tiene_movimientos && (
            <button type="button" className="btn btn-g" style={{ color: "#B91C1C", marginRight: "auto" }} onClick={() => setEliminar(true)}>
              Eliminar cuenta
            </button>
          )}
          <button type="button" className="btn btn-o" onClick={onCancelar}>
            Cancelar
          </button>
          <button type="button" className="btn btn-p" onClick={guardar} disabled={guardando}>
            {guardando ? "Guardando…" : "Guardar cuenta"}
          </button>
        </div>
      </div>
      {eliminar && cuenta && (
        <Modal
          titulo="¿Eliminar esta cuenta?"
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
          <span>
            Se eliminará <b style={{ color: "#111827" }}>{etiquetaCuentaContable(cuenta)}</b> del catálogo.
          </span>
        </Modal>
      )}
    </section>
  );
}

const ORDEN_GRUPOS: GrupoConcepto[] = ["ventas", "compras", "bancos", "activos"];

/** Editor de las cuentas por defecto: qué cuenta usa cada concepto de los asientos automáticos. */
function CuentasPorDefecto({ cuentas, mostrarAviso }: { cuentas: CuentaContable[]; mostrarAviso: MostrarAviso }) {
  const [lista, setLista] = useState<CuentaPorDefecto[]>([]);
  const [valores, setValores] = useState<Record<string, string>>({});
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorGuardar, setErrorGuardar] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const ref = useRef<HTMLElement>(null);
  const { hash } = useLocation();

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      const l = await contabilidadApi.listarCuentasPorDefecto();
      setLista(l);
      setValores(Object.fromEntries(l.map((d) => [d.concepto, d.cuenta_id ?? ""])));
      setError(null);
    } catch (err) {
      setError(mensajeError(err));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  useEffect(() => {
    if (hash === "#por-defecto" && !cargando) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hash, cargando]);

  const hojas = useMemo(() => cuentas.filter((c) => c.acepta_movimientos && c.activa), [cuentas]);
  const cambios = lista.filter((d) => (valores[d.concepto] ?? "") !== (d.cuenta_id ?? ""));
  const faltan = lista.filter((d) => !valores[d.concepto]).length;

  async function guardar() {
    setGuardando(true);
    setErrorGuardar(null);
    try {
      const l = await contabilidadApi.actualizarCuentasPorDefecto(Object.fromEntries(cambios.map((d) => [d.concepto, valores[d.concepto] || null])));
      setLista(l);
      setValores(Object.fromEntries(l.map((d) => [d.concepto, d.cuenta_id ?? ""])));
      mostrarAviso("Cuentas por defecto guardadas", { detalle: "Los asientos ya generados se actualizan al volver a generar el período." });
    } catch (err) {
      setErrorGuardar(mensajeError(err));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <section className="card" id="por-defecto" ref={ref}>
      <div className="card-h">
        <div>
          <h2>Cuentas por defecto de los asientos automáticos</h2>
          <span className="hint">
            Qué cuenta usa cada concepto al contabilizar facturas, cobros, compras, pagos, bancos y depreciación
            {faltan > 0 ? ` · ${faltan} sin asignar` : ""}
          </span>
        </div>
        <button type="button" className="btn btn-p btn-sm" onClick={guardar} disabled={guardando || cambios.length === 0}>
          {guardando ? "Guardando…" : cambios.length ? `Guardar ${cambios.length} ${cambios.length === 1 ? "cambio" : "cambios"}` : "Guardar"}
        </button>
      </div>
      {cargando && lista.length === 0 && <Cargando />}
      {error && (
        <div className="card-b">
          <AvisoError titulo="No se pudieron cargar las cuentas por defecto." mensaje={error} onReintentar={() => void cargar()} />
        </div>
      )}
      {lista.length > 0 && (
        <div className="card-b">
          {errorGuardar && (
            <div className="al al-d" role="alert" style={{ marginBottom: 12 }}>
              <IconAlerta />
              <span>
                <b>{errorGuardar}</b>
              </span>
            </div>
          )}
          <div className="ct-def">
            {ORDEN_GRUPOS.filter((g) => lista.some((d) => d.grupo === g)).map((g) => (
              <GrupoDefecto key={g} titulo={GRUPO_CONCEPTO[g]}>
                {lista
                  .filter((d) => d.grupo === g)
                  .map((d) => {
                    const valor = valores[d.concepto] ?? "";
                    const fuera = valor && !hojas.some((c) => c.id === valor);
                    return (
                      <div key={d.concepto} className="fld">
                        <label className="lbl" htmlFor={`pd-${d.concepto}`}>
                          <span>{d.etiqueta}</span>
                          <span className="hint mono">sugerida {d.codigo_sugerido}</span>
                        </label>
                        <select
                          id={`pd-${d.concepto}`}
                          className={valor ? "inp" : "inp falta"}
                          value={valor}
                          onChange={(e) => setValores((prev) => ({ ...prev, [d.concepto]: e.target.value }))}
                        >
                          <option value="">Sin cuenta (no se contabiliza)</option>
                          {fuera && <option value={valor}>{d.cuenta_codigo ? `${d.cuenta_codigo} · ${d.cuenta_nombre}` : "Cuenta actual"}</option>}
                          {hojas.map((c) => (
                            <option key={c.id} value={c.id}>
                              {etiquetaCuentaContable(c)}
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
              </GrupoDefecto>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function GrupoDefecto({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <>
      <h3 className="ct-def-g">{titulo}</h3>
      {children}
    </>
  );
}
