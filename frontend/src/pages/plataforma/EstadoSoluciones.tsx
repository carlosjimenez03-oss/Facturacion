import { useCallback, useEffect, useState } from "react";
import { IconActualizar } from "../../layout/icons";
import { configuracionApi, type EstadoServicios, type ServicioEstado } from "../../lib/configuracionApi";
import { nombreAmbiente } from "../../lib/marca";
import { mensajeError } from "./configuracionUtil";
import "./plataforma.css";

const ETIQUETA: Record<ServicioEstado["estado"], string> = {
  ok: "En línea",
  error: "Con errores",
  caido: "Sin respuesta",
  no_configurado: "No configurado",
};

/** /estado-soluciones: salud de cada servicio (GET /estado-servicios de erp-api), ambiente DGII incluido. */
export function EstadoSolucionesPage() {
  const [estado, setEstado] = useState<EstadoServicios | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);

  const cargar = useCallback(() => {
    setCargando(true);
    configuracionApi
      .estadoServicios()
      .then((e) => {
        setEstado(e);
        setError(null);
      })
      .catch((err) => setError(mensajeError(err)))
      .finally(() => setCargando(false));
  }, []);

  useEffect(() => {
    cargar();
    const t = window.setInterval(cargar, 30_000);
    return () => window.clearInterval(t);
  }, [cargar]);

  const servicios = estado?.servicios ?? [];
  const grupos = [...new Set(servicios.map((s) => s.grupo))];
  const caidos = servicios.filter((s) => s.estado === "caido" || s.estado === "error").length;
  const ambiente = nombreAmbiente(estado?.ambiente);

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>Estado de las soluciones</h1>
          <p>Servicios de la aplicación, conexión con la DGII, base de datos e inicio de sesión</p>
        </div>
        <button type="button" className="btn btn-o" onClick={cargar} disabled={cargando}>
          <IconActualizar width={16} height={16} />
          {cargando ? "Verificando…" : "Verificar ahora"}
        </button>
      </div>

      {error && (
        <div className="al al-d" role="alert">
          <span>
            <b>No se pudo consultar el estado.</b> {error}
          </span>
        </div>
      )}

      {estado && (
        <div className="grid-stats">
          <div className="card stat" style={{ padding: 16 }}>
            <span className="hint">Ambiente DGII</span>
            <div className="v" style={{ fontSize: 20, fontWeight: 650 }}>{ambiente}</div>
            <span className="hint">
              APP_ENV={estado.ambiente} · {estado.ambiente_dgii}
            </span>
          </div>
          <div className="card stat" style={{ padding: 16 }}>
            <span className="hint">Servicios en línea</span>
            <div className="v" style={{ fontSize: 20, fontWeight: 650 }}>
              {servicios.filter((s) => s.estado === "ok").length} de {servicios.length}
            </div>
            <span className={caidos ? "notif-bad" : "hint"}>{caidos ? `${caidos} con problemas` : "Todo funciona"}</span>
          </div>
          <div className="card stat" style={{ padding: 16 }}>
            <span className="hint">Última verificación</span>
            <div className="v" style={{ fontSize: 20, fontWeight: 650 }}>{new Date(estado.verificado_en).toLocaleTimeString("es-DO")}</div>
            <span className="hint">Se actualiza cada 30 segundos</span>
          </div>
        </div>
      )}

      {grupos.map((g) => (
        <section key={g} className="card">
          <div className="card-h">
            <h2>{g}</h2>
          </div>
          <div className="est-grid">
            {servicios
              .filter((s) => s.grupo === g)
              .map((s) => (
                <div key={s.id} className="est-item">
                  <span className={`est-dot est-${s.estado}`} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <b style={{ display: "block", color: "#111827" }}>{s.nombre}</b>
                    <span className="hint mono" style={{ fontSize: 12 }}>{s.id}</span>
                    {s.detalle && <div className="hint" style={{ overflowWrap: "anywhere" }}>{s.detalle}</div>}
                  </div>
                  <div style={{ textAlign: "right", flex: "none" }}>
                    <span className={s.estado === "ok" ? "bd b-acep nodot" : s.estado === "no_configurado" ? "bd b-borr nodot" : "bd b-rech nodot"}>
                      {ETIQUETA[s.estado]}
                    </span>
                    {s.latencia_ms != null && s.estado === "ok" && <div className="hint">{s.latencia_ms} ms</div>}
                  </div>
                </div>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}
