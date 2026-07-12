---
name: dependency-hygiene
description: >
  Auditoría obligatoria de dependencias. Activa cuando se toca
  requirements.txt o pyproject.toml. Manifiesto único (pyproject.toml como
  fuente de verdad), pins exactos, pip-audit sin CVEs HIGH, sin basura de
  instalación (archivos =X.Y generados por redirección de pip).
  NO activa para cambios de código que no toquen dependencias.
---

# Skill: Dependency Hygiene

## Regla cero (no negociable)

**`pyproject.toml` es la única fuente de verdad para dependencias.**
`requirements.txt` se genera/actualiza desde `pyproject.toml`, nunca al revés.
Cualquier discrepancia entre ambos archivos es un bug que debe corregirse
alineando `requirements.txt` con `pyproject.toml`.

---

## Cuándo activa este skill

- Edición de `requirements.txt` o `pyproject.toml`.
- `pip install` de cualquier paquete nuevo.
- Discusión sobre versiones de dependencias.
- `pip-audit` reporta CVE HIGH o CRITICAL.
- Aparecen archivos basura `=X.Y` en la raíz del proyecto.

NO activa para:
- Cambios en `package.json` del dashboard (frontend independiente).
- `pip install` dentro de Dockerfile (se audita en el build).

---

## Checklist de higiene (obligatorio en cada cambio de dependencias)

### A. Manifiesto único

- [ ] `pyproject.toml` contiene TODAS las dependencias (main + dev).
- [ ] `requirements.txt` es una copia exacta generada desde `pyproject.toml`.
- [ ] No hay dependencias en `requirements.txt` que falten en `pyproject.toml`.
- [ ] No hay dependencias en `pyproject.toml` que falten en `requirements.txt`.

### B. Pins exactos

- [ ] Dependencias de producción pineadas con `==` (no `>=` salvo justificación).
- [ ] SDK de Polymarket pineado exactamente: `py-clob-client-v2==1.0.1`.
- [ ] Dependencias de desarrollo pueden usar `>=` para permitir actualizaciones de seguridad.
- [ ] Ningún paquete tiene versión `latest` o `*`.

### C. Security

- [ ] `pip-audit` no reporta CVEs HIGH o CRITICAL.
- [ ] `bandit -r src/ -c .bandit` limpio.
- [ ] Los hashes de dependencias en `requirements.txt` (si se usan) están actualizados.

### D. Basura de instalación

- [ ] No hay archivos `=X.Y` en la raíz del proyecto (ej. `=0.28`, `=6.100.0`).
- [ ] `.gitignore` incluye el patrón `=*` para prevenir futuros accidentes.
- [ ] `pip install` se ejecuta con `-r requirements.txt` o `-e .`, nunca con `>=`.

### E. Consistencia post-install

- [ ] `pip check` no reporta conflictos de dependencias.
- [ ] `python -c "import src; print('OK')"` funciona sin errores de import.
- [ ] `pytest -xq` pasa con las nuevas versiones.

---

## Versiones pineadas (snapshot R2.5)

| Paquete | Versión | Razón del pin |
|---|---|---|
| `py-clob-client-v2` | `==1.0.1` | SDK oficial — breaking changes frecuentes |
| `fastapi` | `==0.111.0` | Framework estable, pin para reproducibilidad |
| `sqlalchemy` | `==2.0.30` | ORM — cambios de API en minors |
| `asyncpg` | `==0.29.0` | Driver DB — estabilidad |
| `aiogram` | `==3.7.0` | Telegram — breaking changes en minors |
| `uvloop` | `>=0.21.0` | Event loop — compatible upgrades seguros |

---

## Racionalizaciones a rechazar

- *"Añado la dependencia solo en requirements.txt, pyproject.toml no hace falta."* → No. `pyproject.toml` es la fuente de verdad. Siempre en ambos.
- *"Pongo >= para producción, total ya funciona."* → No. Reproducibilidad requiere pins exactos. `>=` solo para dev tools y libs con strong backward compat.
- *"El CVE es solo MEDIUM, no hace falta actualizar."* → Parchear → test → merge. Cero CVEs sin atender.
- *"Instalo con pip install X>=Y y luego actualizo requirements."* → No. Siempre editar `pyproject.toml` primero, luego generar `requirements.txt`.
- *"Los archivos =0.28 no molestan, déjalos."* → No. Son basura de `pip install X >= 0.28` mal redirigido. Borrar + añadir `=*` al `.gitignore`.

---

## Red flags

- `requirements.txt` modificado sin tocar `pyproject.toml`.
- `pip install <paquete>` ejecutado fuera de `pyproject.toml`.
- Archivos `=*` en `git status`.
- `pip-audit` con CVEs sin resolver.
- Versiones `>=` en dependencias de producción críticas (SDK, DB driver, framework).

---

## Salidas esperadas

1. `pyproject.toml` actualizado con la nueva dependencia.
2. `requirements.txt` regenerado y consistente.
3. `pip-audit` limpio.
4. `pytest -xq` verde con las nuevas versiones.
5. `.gitignore` actualizado si se añaden nuevos patrones de archivos basura.
