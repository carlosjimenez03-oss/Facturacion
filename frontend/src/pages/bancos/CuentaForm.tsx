// Alta y edición de una cuenta bancaria (formulario "+ Nueva cuenta" de Bancos.dc.html, ampliado con el
// saldo inicial, la moneda, la cuenta del catálogo y si está activa).
import { useEffect, useState } from "react";
import { IconAlerta } from "../../layout/icons";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria, CuentaBancariaPayload, Moneda, TipoCuentaBancaria } from "../../lib/bancosApi";
import { contabilidadApi } from "../../lib/contabilidadApi";
import type { CuentaContable } from "../../lib/contabilidadApi";
import { Modal } from "../ingresos/IngresosShell";
import { hoyIso, mensajeError } from "../ingresos/ingresosUtil";
import { Interruptor } from "../inventario/InventarioShell";
import { BANCOS_RD, TIPOS_CUENTA } from "./bancosUtil";

interface Campos {
  banco: string;
  tipo: TipoCuentaBancaria;
  numero: string;
  nombre: string;
  moneda: Moneda;
  saldo_inicial: string;
  fecha_saldo_inicial: string;
  cuenta_contable_id: string;
  activa: boolean;
  notas: string;
}

function camposDe(c: CuentaBancaria | null): Campos {
  return {
    banco: c?.banco ?? "Banco Popular Dominicano",
    tipo: c?.tipo ?? "corriente",
    numero: "",
    nombre: c?.nombre ?? "",
    moneda: c?.moneda ?? "DOP",
    saldo_inicial: c ? String(Number(c.saldo_inicial)) : "",
    fecha_saldo_inicial: c?.fecha_saldo_inicial ?? hoyIso(),
    cuenta_contable_id: c?.cuenta_contable_id ?? "",
    activa: c?.activa ?? true,
    notas: c?.notas ?? "",
  };
}

/** Cuenta contable por defecto según el tipo (la que usan los asientos si no se elige otra). */
const DEFECTO_POR_TIPO: Record<TipoCuentaBancaria, string> = {
  corriente: "Bancos (cuenta por defecto)",
  ahorro: "Bancos (cuenta por defecto)",
  caja_chica: "Caja chica (cuenta por defecto)",
  tarjeta: "Tarjetas de crédito (cuenta por defecto)",
};

export function CuentaForm({
  cuenta,
  onGuardada,
  onEliminada,
  onCancelar,
  mostrarAviso,
}: {
  cuenta: CuentaBancaria | null;
  onGuardada: (c: CuentaBancaria, nueva: boolean) => void;
  onEliminada: (c: CuentaBancaria) => void;
  onCancelar: () => void;
  mostrarAviso: (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;
}) {
  const [f, setF] = useState<Campos>(() => camposDe(cuenta));
  const [catalogo, setCatalogo] = useState<CuentaContable[]>([]);
  const [errorCatalogo, setErrorCatalogo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [eliminar, setEliminar] = useState(false);
  const [eliminando, setEliminando] = useState(false);

  useEffect(() => {
    let vigente = true;
    contabilidadApi
      .listarCuentas({ solo_movimiento: true, activa: true })
      .then((lista) => vigente && setCatalogo(lista.filter((c) => c.tipo === "activo" || c.tipo === "pasivo")))
      .catch((err) => vigente && setErrorCatalogo(mensajeError(err)));
    return () => {
      vigente = false;
    };
  }, []);

  function set<K extends keyof Campos>(campo: K, valor: Campos[K]) {
    setF((prev) => ({ ...prev, [campo]: valor, ...(campo === "tipo" && valor === "caja_chica" ? { moneda: "DOP" as Moneda } : {}) }));
    setError(null);
  }

  // La cuenta contable elegida puede no estar en la lista (inactiva o de grupo): se muestra igual.
  const contableActual =
    cuenta?.cuenta_contable_id && !catalogo.some((c) => c.id === cuenta.cuenta_contable_id)
      ? { id: cuenta.cuenta_contable_id, etiqueta: `${cuenta.cuenta_contable_codigo ?? ""} · ${cuenta.cuenta_contable_nombre ?? ""}` }
      : null;

  async function guardar() {
    if (!f.banco.trim()) return setError("Indique el banco (o «Caja chica» para el efectivo).");
    const saldo = f.saldo_inicial.trim() === "" ? 0 : Number(f.saldo_inicial);
    if (!Number.isFinite(saldo)) return setError("El saldo inicial no es un monto válido.");
    if (!f.fecha_saldo_inicial) return setError("Indique la fecha del saldo inicial.");
    setGuardando(true);
    try {
      if (!cuenta) {
        const payload: CuentaBancariaPayload = {
          banco: f.banco.trim(),
          tipo: f.tipo,
          numero: f.numero.trim() || null,
          nombre: f.nombre.trim() || null,
          moneda: f.moneda,
          saldo_inicial: saldo.toFixed(2),
          fecha_saldo_inicial: f.fecha_saldo_inicial,
          cuenta_contable_id: f.cuenta_contable_id || null,
          activa: f.activa,
          notas: f.notas.trim() || null,
        };
        onGuardada(await bancosApi.crearCuenta(payload), true);
      } else {
        // Solo lo que cambió: el backend rechaza (409) cambiar el saldo inicial con conciliaciones cerradas
        // o la moneda con movimientos, aunque se envíe el mismo valor.
        const cambios: Partial<CuentaBancariaPayload> = {};
        if (f.banco.trim() !== cuenta.banco) cambios.banco = f.banco.trim();
        if (f.tipo !== cuenta.tipo) cambios.tipo = f.tipo;
        if (f.numero.trim()) cambios.numero = f.numero.trim();
        if ((f.nombre.trim() || null) !== cuenta.nombre) cambios.nombre = f.nombre.trim() || null;
        if (f.moneda !== cuenta.moneda) cambios.moneda = f.moneda;
        if (saldo.toFixed(2) !== Number(cuenta.saldo_inicial).toFixed(2)) cambios.saldo_inicial = saldo.toFixed(2);
        if (f.fecha_saldo_inicial !== cuenta.fecha_saldo_inicial) cambios.fecha_saldo_inicial = f.fecha_saldo_inicial;
        if ((f.cuenta_contable_id || null) !== cuenta.cuenta_contable_id) cambios.cuenta_contable_id = f.cuenta_contable_id || null;
        if (f.activa !== cuenta.activa) cambios.activa = f.activa;
        if ((f.notas.trim() || null) !== cuenta.notas) cambios.notas = f.notas.trim() || null;
        onGuardada(Object.keys(cambios).length ? await bancosApi.actualizarCuenta(cuenta.id, cambios) : cuenta, false);
      }
    } catch (err) {
      setError(mensajeError(err));
      setGuardando(false);
    }
  }

  async function confirmarEliminar() {
    if (!cuenta) return;
    setEliminando(true);
    try {
      await bancosApi.eliminarCuenta(cuenta.id);
      onEliminada(cuenta);
    } catch (err) {
      setEliminar(false);
      setEliminando(false);
      mostrarAviso("No se pudo eliminar la cuenta", {
        detalle: `${mensajeError(err)}. Si ya tiene movimientos, desactívela: dejará de ofrecerse en cobros, pagos y transferencias.`,
        error: true,
      });
    }
  }

  const caja = f.tipo === "caja_chica";

  return (
    <section className="card no-imprimir">
      <div className="card-h">
        <h2>{cuenta ? `Editar cuenta · ${cuenta.nombre}` : "Nueva cuenta bancaria"}</h2>
        <span className="hint">El saldo actual = saldo inicial + movimientos desde esa fecha</span>
      </div>
      <div className="card-b form-grid">
        <div className="fld">
          <label className="lbl" htmlFor="bb">
            Banco <span className="req">*</span>
          </label>
          <input id="bb" className="inp" list="bb-lista" value={f.banco} onChange={(e) => set("banco", e.target.value)} placeholder="Banco Popular Dominicano" />
          <datalist id="bb-lista">
            {BANCOS_RD.map((b) => (
              <option key={b} value={b} />
            ))}
          </datalist>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bt">Tipo</label>
          <select id="bt" className="inp" value={f.tipo} onChange={(e) => set("tipo", e.target.value as TipoCuentaBancaria)}>
            {TIPOS_CUENTA.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bn">Número de cuenta</label>
          <input
            id="bn"
            className="inp mono"
            value={f.numero}
            onChange={(e) => set("numero", e.target.value)}
            placeholder={cuenta?.numero_enmascarado ? `${cuenta.numero_enmascarado} (sin cambios)` : caja ? "CAJA-01" : "000-000000-0"}
            autoComplete="off"
          />
          <span className="hint">Solo se muestran los últimos 4 dígitos.</span>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bnom">Nombre para mostrar</label>
          <input id="bnom" className="inp" value={f.nombre} onChange={(e) => set("nombre", e.target.value)} placeholder={`${f.banco || "Banco"} · ${TIPOS_CUENTA.find((t) => t.value === f.tipo)?.label}`} />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bmon">Moneda</label>
          <select id="bmon" className="inp" value={f.moneda} onChange={(e) => set("moneda", e.target.value as Moneda)} disabled={caja}>
            <option value="DOP">Pesos dominicanos (RD$)</option>
            <option value="USD">Dólares (US$)</option>
          </select>
          {f.moneda === "USD" && <span className="hint">Los cobros y pagos en RD$ no pueden usar una cuenta en dólares.</span>}
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bsal">Saldo inicial ({f.moneda === "USD" ? "US$" : "RD$"})</label>
          <input
            id="bsal"
            type="number"
            step="0.01"
            className="inp"
            style={{ textAlign: "right" }}
            value={f.saldo_inicial}
            onChange={(e) => set("saldo_inicial", e.target.value)}
            placeholder="0.00"
          />
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bfec">Saldo al empezar el día</label>
          <input id="bfec" type="date" className="inp" value={f.fecha_saldo_inicial} max={hoyIso()} onChange={(e) => set("fecha_saldo_inicial", e.target.value)} />
          <span className="hint">No se registran movimientos anteriores a esta fecha.</span>
        </div>
        <div className="fld">
          <label className="lbl" htmlFor="bcc">Cuenta contable</label>
          <select id="bcc" className="inp" value={f.cuenta_contable_id} onChange={(e) => set("cuenta_contable_id", e.target.value)}>
            <option value="">{DEFECTO_POR_TIPO[f.tipo]}</option>
            {contableActual && <option value={contableActual.id}>{contableActual.etiqueta}</option>}
            {catalogo.map((c) => (
              <option key={c.id} value={c.id}>
                {c.codigo} · {c.nombre}
              </option>
            ))}
          </select>
          {errorCatalogo && <span className="hint err-t">No se pudo cargar el catálogo de cuentas: se usará la cuenta por defecto.</span>}
        </div>
        <div className="fld fld-full">
          <label className="lbl" htmlFor="bnot">Notas</label>
          <input id="bnot" className="inp" value={f.notas} onChange={(e) => set("notas", e.target.value)} placeholder="Firmantes, sucursal, oficial de cuenta…" />
        </div>
        <div className="fld-full">
          <Interruptor
            id="bact"
            etiqueta="Cuenta activa"
            ayuda="Las cuentas inactivas conservan su historial pero no se ofrecen en cobros, pagos ni transferencias."
            valor={f.activa}
            onCambiar={(v) => set("activa", v)}
          />
        </div>
        {error && (
          <div className="al al-d fld-full" role="alert">
            <IconAlerta />
            <span>
              <b>{error}</b>
            </span>
          </div>
        )}
        <div className="row fld-full" style={{ justifyContent: "flex-end" }}>
          {cuenta && (
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
              <button type="button" className="btn btn-d" onClick={confirmarEliminar} disabled={eliminando}>
                {eliminando ? "Eliminando…" : "Sí, eliminar"}
              </button>
            </>
          }
        >
          <span>
            Se eliminará <b style={{ color: "#111827" }}>{cuenta.nombre}</b>. Solo se puede eliminar una cuenta sin movimientos, cobros, pagos ni extractos importados; si ya los
            tiene, desactívela.
          </span>
        </Modal>
      )}
    </section>
  );
}
