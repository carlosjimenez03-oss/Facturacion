// Bancos (Bancos.dc.html del prototipo): tarjetas de las cuentas bancarias con su saldo y las pestañas
// Movimientos (/bancos/cuentas), Conciliación (/bancos/conciliacion) y Transferencias (/bancos/transferencias).
// La cuenta elegida viaja en ?cuenta=<id> para que se mantenga al cambiar de pestaña.
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { bancosApi } from "../../lib/bancosApi";
import type { CuentaBancaria } from "../../lib/bancosApi";
import { AvisoError, Cargando, Vacio } from "../ingresos/IngresosShell";
import { useAviso } from "../ingresos/useAviso";
import { mensajeError } from "../ingresos/ingresosUtil";
import "../ingresos/ingresos.css";
import "./bancos.css";
import { estadoTarjeta, montoMoneda, TIPO_CUENTA_LABEL } from "./bancosUtil";
import { ConciliacionTab } from "./Conciliacion";
import { CuentaForm } from "./CuentaForm";
import { MovimientosTab } from "./Movimientos";
import { TransferenciasTab } from "./Transferencias";

type Pestana = "cuentas" | "conciliacion" | "transferencias";

const PESTANAS: { id: Pestana; label: string }[] = [
  { id: "cuentas", label: "Movimientos" },
  { id: "conciliacion", label: "Conciliación" },
  { id: "transferencias", label: "Transferencias" },
];

/** Lo que reciben las pestañas: la cuenta elegida, todas las cuentas y cómo refrescar los saldos. */
export interface PestanaBancosProps {
  cuenta: CuentaBancaria;
  cuentas: CuentaBancaria[];
  recargarCuentas: () => void;
  mostrarAviso: (texto: string, opciones?: { detalle?: string; error?: boolean }) => void;
  onEditarCuenta: () => void;
}

export function BancosPage() {
  const { tab } = useParams<{ tab: string }>();
  const pestana = PESTANAS.find((p) => p.id === tab);
  if (!pestana) return <Navigate to="/bancos/cuentas" replace />;
  return <Bancos pestana={pestana.id} />;
}

function Bancos({ pestana }: { pestana: Pestana }) {
  const [params, setParams] = useSearchParams();
  const [cuentas, setCuentas] = useState<CuentaBancaria[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<{ cuenta: CuentaBancaria | null } | null>(null);
  const [toast, mostrarAviso] = useAviso();

  const cargar = useCallback(async (silencioso = false) => {
    if (!silencioso) setCargando(true);
    try {
      setCuentas(await bancosApi.listarCuentas());
      setError(null);
    } catch (err) {
      if (!silencioso) setCuentas([]);
      setError(mensajeError(err));
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const cuentaParam = params.get("cuenta");
  const cuenta = cuentas.find((c) => c.id === cuentaParam) ?? cuentas.find((c) => c.activa) ?? cuentas[0] ?? null;
  const recargarCuentas = useCallback(() => void cargar(true), [cargar]);

  function elegir(id: string) {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("cuenta", id);
        return p;
      },
      { replace: true },
    );
  }

  function guardada(c: CuentaBancaria, nueva: boolean) {
    setForm(null);
    mostrarAviso(nueva ? "Cuenta bancaria agregada" : "Cuenta actualizada", { detalle: `${c.nombre}${c.numero_enmascarado ? ` · ${c.numero_enmascarado}` : ""}` });
    void cargar(true);
    elegir(c.id);
  }

  function eliminada(c: CuentaBancaria) {
    setForm(null);
    mostrarAviso("Cuenta eliminada", { detalle: c.nombre });
    setParams({}, { replace: true });
    void cargar(true);
  }

  const consulta = cuenta ? `?cuenta=${cuenta.id}` : "";
  const nueva = form && !form.cuenta;

  return (
    <div className="content">
      <div className="ph no-imprimir">
        <div>
          <div className="crumb">
            <span>Bancos</span>
          </div>
          <h1>Bancos</h1>
          <p>Cuentas, conciliación bancaria y transferencias</p>
        </div>
        <button type="button" className="btn btn-p" onClick={() => setForm(nueva ? null : { cuenta: null })}>
          {nueva ? "Cancelar" : "+ Nueva cuenta"}
        </button>
      </div>

      {form && (
        <CuentaForm
          key={`form-${form.cuenta?.id ?? "nueva"}`}
          cuenta={form.cuenta}
          onGuardada={guardada}
          onEliminada={eliminada}
          onCancelar={() => setForm(null)}
          mostrarAviso={mostrarAviso}
        />
      )}

      {error && <AvisoError titulo="No se pudieron cargar las cuentas bancarias." mensaje={error} onReintentar={() => void cargar()} />}

      {cargando && cuentas.length === 0 && (
        <section className="card">
          <Cargando />
        </section>
      )}

      {!cargando && !error && cuentas.length === 0 && !form && (
        <section className="card">
          <Vacio
            titulo="Aún no hay cuentas bancarias"
            texto="Registre sus cuentas corrientes, de ahorro, la caja chica y las tarjetas de crédito de la empresa para llevar sus movimientos, conciliarlas con el banco y transferir entre ellas."
          >
            <button type="button" className="btn btn-p btn-sm" onClick={() => setForm({ cuenta: null })}>
              + Nueva cuenta
            </button>
          </Vacio>
        </section>
      )}

      {cuentas.length > 0 && (
        <div className="bk-cuentas no-imprimir" role="listbox" aria-label="Cuentas bancarias">
          {cuentas.map((c) => {
            const sel = c.id === cuenta?.id;
            return (
              <button
                key={c.id}
                type="button"
                role="option"
                aria-selected={sel}
                className={`card bk-cta${sel ? " sel" : ""}${c.activa ? "" : " off"}`}
                onClick={() => elegir(c.id)}
              >
                <span className="bk-cta-h">
                  <span>{c.nombre}</span>
                  <span className="mono">{c.numero_enmascarado ?? (c.tipo === "caja_chica" ? "Efectivo" : "")}</span>
                </span>
                <span className="bk-cta-s">{montoMoneda(c.saldo_actual, c.moneda)}</span>
                <span className="hint">
                  {TIPO_CUENTA_LABEL[c.tipo] ?? c.tipo}
                  {c.moneda === "USD" ? " · USD" : ""} · {estadoTarjeta(c)}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {cuentas.length > 0 && (
        <nav className="tabs no-imprimir" aria-label="Bancos">
          {PESTANAS.map((p) => (
            <Link key={p.id} to={`/bancos/${p.id}${consulta}`} className={p.id === pestana ? "tab on" : "tab"} aria-current={p.id === pestana ? "page" : undefined}>
              {p.label}
            </Link>
          ))}
        </nav>
      )}

      {cuenta && pestana === "cuentas" && (
        <MovimientosTab
          key={`mov-${cuenta.id}`}
          cuenta={cuenta}
          cuentas={cuentas}
          recargarCuentas={recargarCuentas}
          mostrarAviso={mostrarAviso}
          onEditarCuenta={() => setForm({ cuenta })}
        />
      )}
      {cuenta && pestana === "conciliacion" && (
        <ConciliacionTab
          key={`conc-${cuenta.id}`}
          cuenta={cuenta}
          cuentas={cuentas}
          recargarCuentas={recargarCuentas}
          mostrarAviso={mostrarAviso}
          onEditarCuenta={() => setForm({ cuenta })}
        />
      )}
      {cuenta && pestana === "transferencias" && (
        <TransferenciasTab
          cuenta={cuenta}
          cuentas={cuentas}
          recargarCuentas={recargarCuentas}
          mostrarAviso={mostrarAviso}
          onEditarCuenta={() => setForm({ cuenta })}
        />
      )}

      {toast}
    </div>
  );
}
