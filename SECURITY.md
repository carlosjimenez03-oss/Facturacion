# Politica de seguridad

Este repositorio contiene el sistema de facturacion electronica (e-CF) y los
servicios asociados que operan contra la DGII. Los reportes de seguridad se
tratan con prioridad sobre cualquier otro trabajo.

## Versiones soportadas

Solo las ramas activas reciben parches de seguridad:

| Rama | Estado | Soporte de seguridad |
| ---- | ------ | -------------------- |
| `main` | Produccion | Si |
| `qa` | Certificacion DGII (CerteCF) | Si |
| `develop` | Integracion | Solo fallos criticos |
| Cualquier otra rama o fork | - | No |

Las imagenes publicadas en GHCR solo son soportadas si fueron construidas desde
un commit de `main` o `qa`. No se emiten parches para imagenes antiguas: hay
que redeployar desde la rama soportada.

## Como reportar una vulnerabilidad

**No abras un issue publico, ni un pull request, ni lo comentes en redes.**

Usa el reporte privado de GitHub:

1. Ve a la pestana **Security** del repositorio.
2. Pulsa **Report a vulnerability** (GitHub Private Vulnerability Reporting).
3. Describe el fallo con: version o commit afectado, pasos para reproducirlo,
   impacto que crees que tiene y, si puedes, una prueba de concepto.

Si el reporte privado no esta disponible, escribe al mantenedor del repositorio
a traves del correo asociado a la cuenta `@carlosjimenez03-oss` en GitHub.

Incluye siempre el commit exacto (`git rev-parse HEAD`) o la etiqueta de la
imagen (`ghcr.io/...:tag`) donde lo observaste.

## Que esperar

| Etapa | Plazo objetivo |
| ----- | -------------- |
| Acuse de recibo | 3 dias habiles |
| Evaluacion inicial y severidad | 7 dias habiles |
| Correccion o plan de mitigacion | 30 dias (critica/alta), 90 dias (media/baja) |
| Divulgacion coordinada | Tras publicar el parche, o 90 dias desde el reporte |

Si la vulnerabilidad esta siendo explotada activamente, dilo en el reporte: se
prioriza de inmediato y se coordina contigo la divulgacion.

## Alcance

Dentro del alcance:

- Codigo de este repositorio y de las imagenes publicadas en GHCR.
- Los flujos de autenticacion, autorizacion y manejo de tokens del ERP.
- El manejo de credenciales de la DGII, certificados `.p12` y secretos.
- Inyeccion SQL, XSS, SSRF, IDOR y fallos de logica de negocio en los modulos
  `services/*`.
- Los workflows de GitHub Actions de `.github/workflows/`.

Fuera del alcance:

- Infraestructura de GitHub, la DGII o cualquier tercero. No se autoriza probar
  contra sistemas que no sean de este proyecto; esos reportes van al programa de
  bug bounty del proveedor correspondiente.
- Ataques de denegacion de servicio, spam, ingenieria social o acceso fisico.
- Resultados de escaneres automaticos sin impacto demostrado.
- Versiones o forks que no correspondan a `main` o `qa`.

## Practicas del proyecto

- Los secretos viven en el gestor de secretos del entorno (Dokploy / GitHub
  Actions), nunca en el repositorio. `.env.production` y `.env.qa` estan
  prohibidos en el arbol de trabajo.
- Las GitHub Actions se referencian por SHA completo, con permisos minimos
  (`permissions: contents: read` por defecto).
- `main` esta protegida: sin push directo, cambios via pull request.
- Dependabot, CodeQL y el reporte privado de vulnerabilidades estan activos.
- Cualquier hallazgo que afecte a datos de contribuyentes se trata como
  confidencial hasta su correccion.
