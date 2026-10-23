import type { ReactNode, SVGProps } from "react";

/** Íconos de línea (24x24, trazo actual). Mantienen el estilo del prototipo de diseño. */
function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconInicio = () => (
  <Icon>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V21h14V9.5" />
    <path d="M10 21v-6h4v6" />
  </Icon>
);
export const IconBandeja = () => (
  <Icon>
    <path d="M22 12h-6l-2 3h-4l-2-3H2" />
    <path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z" />
  </Icon>
);
export const IconIngresos = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M15 9l-6 6M9 9.5V15h5.5" />
  </Icon>
);
export const IconCompras = () => (
  <Icon>
    <circle cx="12" cy="12" r="9" />
    <path d="M9 15l6-6M9.5 9H15v5.5" />
  </Icon>
);
export const IconContactos = () => (
  <Icon>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8" />
  </Icon>
);
export const IconInventario = () => (
  <Icon>
    <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
    <path d="M3 8l9 5 9-5M12 13v8" />
  </Icon>
);
export const IconBancos = () => (
  <Icon>
    <path d="M3 21h18M5 21v-9M9.5 21v-9M14.5 21v-9M19 21v-9M2.5 9.5 12 4l9.5 5.5z" />
  </Icon>
);
export const IconContabilidad = () => (
  <Icon>
    <rect x="5" y="2.5" width="14" height="19" rx="2" />
    <path d="M8.5 6.5h7M8.5 11h.01M12 11h.01M15.5 11h.01M8.5 14.5h.01M12 14.5h.01M15.5 14.5h.01M8.5 18h7" />
  </Icon>
);
export const IconImpuestos = () => (
  <Icon>
    <path d="M6 2.5h12v19l-3-2-3 2-3-2-3 2z" />
    <path d="M9.5 14.5l5-6M9.8 9h.01M14.2 14h.01" />
  </Icon>
);
export const IconReportes = () => (
  <Icon>
    <path d="M3 17l6-6 4 4 8-8" />
    <path d="M15 7h6v6" />
  </Icon>
);
export const IconTareas = () => (
  <Icon>
    <rect x="5.5" y="4" width="13" height="17" rx="2" />
    <path d="M9 4V2.5h6V4M9.5 12.5l2 2 3.5-4" />
  </Icon>
);
export const IconSync = () => (
  <Icon>
    <path d="M20.5 13a8.5 8.5 0 0 1-15 4.5L3 15.5M3.5 11a8.5 8.5 0 0 1 15-4.5L21 8.5" />
    <path d="M21 3.5v5h-5M3 20.5v-5h5" />
  </Icon>
);
export const IconConfig = () => (
  <Icon>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1" />
  </Icon>
);
export const IconNomina = () => (
  <Icon>
    <circle cx="12" cy="12" r="9.5" />
    <circle cx="9" cy="10" r="2" />
    <circle cx="15" cy="10" r="2" />
    <path d="M5.5 17a3.8 3.8 0 0 1 7 0M11.5 17a3.8 3.8 0 0 1 7 0" />
  </Icon>
);
export const IconPos = () => (
  <Icon>
    <rect x="3" y="10" width="18" height="10" rx="2" />
    <path d="M7 10V4h10v6M7 14h.01M11 14h.01M15 14h.01M7 17h10" />
  </Icon>
);
export const IconPortal = () => (
  <Icon>
    <path d="M5 15c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.1 2.1 0 0 0-2.9-.1z" />
    <path d="M12 15l-3-3a22 22 0 0 1 2-4A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22 22 0 0 1-4 2z" />
  </Icon>
);
export const IconChevron = (props: SVGProps<SVGSVGElement>) => (
  <Icon strokeWidth={1.8} {...props}>
    <path d="m6 9 6 6 6-6" />
  </Icon>
);
export const IconPanel = () => (
  <Icon>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9 4v16" />
  </Icon>
);
export const IconMenu = () => (
  <Icon strokeWidth={1.8}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Icon>
);
export const IconBuscar = () => (
  <Icon strokeWidth={1.8}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </Icon>
);
export const IconMas = () => (
  <Icon strokeWidth={2}>
    <path d="M12 5v14M5 12h14" />
  </Icon>
);
export const IconAyuda = () => (
  <Icon>
    <circle cx="12" cy="12" r="9.5" />
    <path d="M9.5 9a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 3.6M12 17h.01" />
  </Icon>
);
export const IconCampana = () => (
  <Icon>
    <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.9 1.9 0 0 0 3.4 0" />
  </Icon>
);
export const IconApps = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    {[5, 12, 19].flatMap((y) => [5, 12, 19].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r={1.7} />))}
  </svg>
);
export const IconHoja = () => (
  <Icon strokeWidth={1.8}>
    <path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10z" />
    <path d="M2 21c0-3 1.9-5.4 5.1-6" />
  </Icon>
);
export const IconCerrar = () => (
  <Icon strokeWidth={2}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Icon>
);
export const IconCheck = () => (
  <Icon strokeWidth={2.2}>
    <path d="M20 6 9 17l-5-5" />
  </Icon>
);
export const IconAlerta = () => (
  <Icon strokeWidth={1.8}>
    <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 17h.01" />
  </Icon>
);
export const IconRechazo = () => (
  <Icon strokeWidth={2}>
    <circle cx="12" cy="12" r="9" />
    <path d="M15 9l-6 6M9 9l6 6" />
  </Icon>
);
export const IconDocumento = () => (
  <Icon>
    <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
    <path d="M14 3v6h6" />
  </Icon>
);
export const IconSalir = () => (
  <Icon>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Icon>
);

/** Logotipo: documento con marca de verificación sobre fondo teal. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#0F766E" />
      <path d="M10.5 7.5h8l4 4v13h-12z" fill="none" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
      <path d="M13.5 17.5l2.4 2.4 4.4-4.9" fill="none" stroke="#5EEAD4" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
