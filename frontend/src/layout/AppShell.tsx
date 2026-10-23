import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/** Estructura general: menú lateral fijo + barra superior + contenido. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <Sidebar />
      <div className="main">
        <Topbar />
        <main className="page">{children}</main>
      </div>
    </div>
  );
}
