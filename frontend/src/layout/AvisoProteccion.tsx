import { useEffect, useState, type CSSProperties } from "react";
import { apisProtegidas } from "../lib/sesion";

/**
 * Aviso ROJO cuando el frontend pide inicio de sesión pero las APIs no lo
 * exigen (nginx sin KONTARA_AUTH, o erp-api sin SUPABASE_JWT_SECRET): la
 * pantalla de login daría una falsa sensación de seguridad. Se comprueba una
 * vez al cargar (`apisProtegidas`, ver lib/sesion.ts y docs/AUTH.md).
 */
export function AvisoProteccion({ estilo }: { estilo?: CSSProperties }) {
  const [abiertas, setAbiertas] = useState(false);
  useEffect(() => {
    let vivo = true;
    void apisProtegidas().then((protegidas) => vivo && setAbiertas(protegidas === false));
    return () => {
      vivo = false;
    };
  }, []);
  if (!abiertas) return null;
  return (
    <div className="aviso-auth aviso-auth-rojo" role="alert" style={estilo}>
      <span>
        <b>Las APIs no están protegidas.</b> Esta aplicación pide inicio de sesión, pero el servidor responde sin él. Revise{" "}
        <code>SUPABASE_JWT_SECRET</code> y <code>KONTARA_AUTH</code> en Dokploy y vuelva a desplegar (ver <code>docs/AUTH.md</code>).
      </span>
    </div>
  );
}
