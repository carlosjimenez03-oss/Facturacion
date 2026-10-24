import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { actualizarPerfil, AUTH_CONFIGURADA, AuthError, cambiarContrasena, cerrarSesion } from "../../lib/sesion";
import { useAviso } from "../ingresos/useAviso";
import { useSesion } from "./useSesion";
import "./plataforma.css";

function mensaje(err: unknown): string {
  return err instanceof AuthError ? err.message : "No se pudo completar. Intente de nuevo.";
}

/** /cuenta/perfil y /cuenta/seguridad: datos y contraseña del usuario en Supabase Auth. */
export function CuentaPage() {
  const { seccion = "perfil" } = useParams();
  const sesion = useSesion();
  const navigate = useNavigate();
  const [aviso, mostrar] = useAviso();
  const [nombre, setNombre] = useState(sesion?.usuario.nombre ?? "");
  const [telefono, setTelefono] = useState(sesion?.usuario.telefono ?? "");
  const [clave, setClave] = useState("");
  const [clave2, setClave2] = useState("");
  const [ocupado, setOcupado] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!AUTH_CONFIGURADA || !sesion) {
    return (
      <div className="content">
        <div className="ph">
          <div>
            <h1>{seccion === "seguridad" ? "Seguridad" : "Mi perfil"}</h1>
          </div>
        </div>
        <div className="al al-w">
          <span>
            El inicio de sesión no está configurado: no hay un usuario al que asociar el perfil. Vea <code>docs/AUTH.md</code>.
          </span>
        </div>
      </div>
    );
  }

  async function correr(fn: () => Promise<unknown>, ok: string) {
    setError(null);
    setOcupado(true);
    try {
      await fn();
      mostrar(ok);
      return true;
    } catch (err) {
      setError(mensaje(err));
      return false;
    } finally {
      setOcupado(false);
    }
  }

  async function guardarPerfil(e: FormEvent) {
    e.preventDefault();
    await correr(() => actualizarPerfil({ nombre, telefono }), "Perfil actualizado");
  }

  async function guardarClave(e: FormEvent) {
    e.preventDefault();
    if (clave.length < 8) return setError("La contraseña debe tener al menos 8 caracteres.");
    if (clave !== clave2) return setError("Las contraseñas no coinciden.");
    if (await correr(() => cambiarContrasena(clave), "Contraseña cambiada")) {
      setClave("");
      setClave2("");
    }
  }

  return (
    <div className="content">
      <div className="ph">
        <div>
          <h1>{seccion === "seguridad" ? "Seguridad" : "Mi perfil"}</h1>
          <p>{sesion.usuario.email}</p>
        </div>
        <div className="seg" role="tablist">
          <Link to="/cuenta/perfil" className={seccion === "perfil" ? "btn btn-sm on" : "btn btn-g btn-sm"}>
            Mi perfil
          </Link>
          <Link to="/cuenta/seguridad" className={seccion === "seguridad" ? "btn btn-sm on" : "btn btn-g btn-sm"}>
            Seguridad
          </Link>
        </div>
      </div>
      {error && (
        <div className="al al-d" role="alert">
          <span>{error}</span>
        </div>
      )}

      {seccion === "seguridad" ? (
        <>
          <form className="card" onSubmit={guardarClave}>
            <div className="card-h">
              <h2>Cambiar contraseña</h2>
            </div>
            <div className="card-b cfg-grid">
              <div className="fld">
                <label className="lbl" htmlFor="seg-n">Nueva contraseña</label>
                <input id="seg-n" className="inp" type="password" autoComplete="new-password" value={clave} onChange={(e) => setClave(e.target.value)} />
              </div>
              <div className="fld">
                <label className="lbl" htmlFor="seg-r">Repita la contraseña</label>
                <input id="seg-r" className="inp" type="password" autoComplete="new-password" value={clave2} onChange={(e) => setClave2(e.target.value)} />
              </div>
              <div className="cfg-full">
                <button type="submit" className="btn btn-p" disabled={ocupado || !clave}>
                  Guardar contraseña
                </button>
              </div>
            </div>
          </form>
          <section className="card">
            <div className="card-h">
              <h2>Sesiones</h2>
            </div>
            <div className="card-b stack-sm">
              <span style={{ fontSize: 13.5, color: "#374151" }}>
                Si usó la aplicación en otro equipo o perdió un dispositivo, cierre las demás sesiones: tendrán que volver a iniciar sesión.
              </span>
              <div className="cfg-acciones">
                <button type="button" className="btn btn-o" disabled={ocupado} onClick={() => void correr(() => cerrarSesion("others"), "Se cerraron las demás sesiones")}>
                  Cerrar las demás sesiones
                </button>
                <button
                  type="button"
                  className="btn btn-g"
                  disabled={ocupado}
                  onClick={async () => {
                    await cerrarSesion("global");
                    navigate("/login", { replace: true });
                  }}
                >
                  Cerrar sesión en todos los dispositivos
                </button>
              </div>
            </div>
          </section>
        </>
      ) : (
        <form className="card" onSubmit={guardarPerfil}>
          <div className="card-h">
            <h2>Datos personales</h2>
          </div>
          <div className="card-b cfg-grid">
            <div className="fld">
              <label className="lbl" htmlFor="pf-n">Nombre completo</label>
              <input id="pf-n" className="inp" value={nombre} onChange={(e) => setNombre(e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pf-t">Teléfono</label>
              <input id="pf-t" className="inp" type="tel" value={telefono} onChange={(e) => setTelefono(e.target.value)} />
            </div>
            <div className="fld">
              <label className="lbl" htmlFor="pf-c">Correo</label>
              <input id="pf-c" className="inp" value={sesion.usuario.email} disabled />
            </div>
            <div className="cfg-full">
              <button type="submit" className="btn btn-p" disabled={ocupado}>
                Guardar perfil
              </button>
            </div>
          </div>
        </form>
      )}
      {aviso}
    </div>
  );
}
