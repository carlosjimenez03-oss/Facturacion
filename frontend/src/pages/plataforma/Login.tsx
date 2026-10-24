import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../../layout/icons";
import { MARCA } from "../../lib/marca";
import { AUTH_CONFIGURADA, AuthError, cambiarContrasena, capturarSesionDeUrl, iniciarSesion, recuperarContrasena } from "../../lib/sesion";
import { useSesion } from "./useSesion";
import "./plataforma.css";

type Modo = "entrar" | "recuperar" | "nueva";

function mensaje(err: unknown): string {
  return err instanceof AuthError ? err.message : "No se pudo completar la operación. Intente de nuevo.";
}

/** /login: correo + contraseña contra Supabase Auth, recuperación por correo y nueva contraseña. */
export function Login() {
  const sesion = useSesion();
  const navigate = useNavigate();
  const location = useLocation();
  const desde = (location.state as { desde?: string } | null)?.desde || "/";
  const [modo, setModo] = useState<Modo>("entrar");
  const [correo, setCorreo] = useState("");
  const [clave, setClave] = useState("");
  const [clave2, setClave2] = useState("");
  const [verClave, setVerClave] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  // Enlace del correo de recuperación/invitación (#access_token=…&type=recovery).
  useEffect(() => {
    capturarSesionDeUrl()
      .then((habia) => habia && setModo("nueva"))
      .catch((err) => setError(mensaje(err)));
  }, []);

  useEffect(() => {
    if (sesion?.recuperacion) setModo("nueva");
  }, [sesion]);

  if (!AUTH_CONFIGURADA) return <Navigate to="/" replace />;
  if (sesion && !sesion.recuperacion && modo !== "nueva") return <Navigate to={desde} replace />;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setAviso(null);
    setEnviando(true);
    try {
      if (modo === "entrar") {
        await iniciarSesion(correo, clave);
        navigate(desde, { replace: true });
      } else if (modo === "recuperar") {
        await recuperarContrasena(correo);
        setAviso(`Si ${correo.trim()} tiene una cuenta, recibirá un enlace para elegir una nueva contraseña.`);
      } else {
        if (clave.length < 8) throw new AuthError(400, "La contraseña debe tener al menos 8 caracteres.");
        if (clave !== clave2) throw new AuthError(400, "Las contraseñas no coinciden.");
        await cambiarContrasena(clave);
        navigate("/", { replace: true });
      }
    } catch (err) {
      setError(mensaje(err));
    } finally {
      setEnviando(false);
    }
  }

  const titulo = modo === "entrar" ? "Inicie sesión" : modo === "recuperar" ? "Recuperar contraseña" : "Elija una nueva contraseña";

  return (
    <div className="login">
      <form className="login-card" onSubmit={enviar} noValidate>
        <div className="login-marca">
          <Logo />
          <span>{MARCA}</span>
        </div>
        <div>
          <h1>{titulo}</h1>
          <p className="hint">
            {modo === "entrar" && "Facturación electrónica, inventario e impuestos de su empresa."}
            {modo === "recuperar" && "Le enviaremos un enlace a su correo para elegir una nueva contraseña."}
            {modo === "nueva" && `Cuenta ${sesion?.usuario.email ?? ""}. Use al menos 8 caracteres.`}
          </p>
        </div>

        {error && (
          <div className="al al-d" role="alert">
            <span>{error}</span>
          </div>
        )}
        {aviso && (
          <div className="al al-s" role="status">
            <span>{aviso}</span>
          </div>
        )}

        {modo !== "nueva" && (
          <div className="fld">
            <label className="lbl" htmlFor="login-correo">Correo electrónico</label>
            <input
              id="login-correo"
              className="inp"
              type="email"
              autoComplete="username"
              autoFocus
              required
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              placeholder="usted@empresa.do"
            />
          </div>
        )}
        {modo !== "recuperar" && (
          <div className="fld">
            <label className="lbl" htmlFor="login-clave">{modo === "nueva" ? "Nueva contraseña" : "Contraseña"}</label>
            <div className="login-clave">
              <input
                id="login-clave"
                className="inp"
                type={verClave ? "text" : "password"}
                autoComplete={modo === "nueva" ? "new-password" : "current-password"}
                required
                value={clave}
                onChange={(e) => setClave(e.target.value)}
              />
              <button type="button" className="btn btn-g btn-sm" onClick={() => setVerClave((v) => !v)} aria-pressed={verClave}>
                {verClave ? "Ocultar" : "Mostrar"}
              </button>
            </div>
          </div>
        )}
        {modo === "nueva" && (
          <div className="fld">
            <label className="lbl" htmlFor="login-clave2">Repita la contraseña</label>
            <input id="login-clave2" className="inp" type={verClave ? "text" : "password"} autoComplete="new-password" value={clave2} onChange={(e) => setClave2(e.target.value)} />
          </div>
        )}

        <button type="submit" className="btn btn-p login-btn" disabled={enviando || (modo !== "nueva" && !correo.trim())}>
          {enviando ? "Un momento…" : modo === "entrar" ? "Entrar" : modo === "recuperar" ? "Enviar enlace" : "Guardar contraseña"}
        </button>

        {modo === "entrar" && (
          <button type="button" className="login-link" onClick={() => { setModo("recuperar"); setError(null); }}>
            ¿Olvidó su contraseña?
          </button>
        )}
        {modo === "recuperar" && (
          <button type="button" className="login-link" onClick={() => { setModo("entrar"); setError(null); setAviso(null); }}>
            Volver a iniciar sesión
          </button>
        )}
        <p className="hint login-pie">¿No tiene cuenta? Pídale acceso al administrador de su empresa.</p>
      </form>
    </div>
  );
}
