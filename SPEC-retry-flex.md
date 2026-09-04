# Spec: Retry / Flex / Bear Trap resiliente

## Objective

Hoy exito y error reprograman igual (`tool_instances_controller.py:737-750`):
`next_run_time = now + reschedule_seconds` en ambos casos. Una tarea que
falla espera 7-12h como si hubiese ido bien.

Queremos:

1. Cada tarea tiene `success` (defecto al completar), `retry_error` (corto
   al fallar) y sigue usando el tiempo exacto (timer OCR / UTC / marcha)
   cuando se puede leer.
2. Solapar aperturas: las tareas toleran `early` (adelanto) y `late`
   (retraso) para hacerlas juntas y evitar cerrar/reabrir el emulador por
   gaps de minutos. Solo aplica a ciclos completados, nunca a errores.
3. Bear trap es especial: dura 5min prep + 30min de rallies. Si el juego se
   cierra dentro de la ventana, se reabre y se sigue intentando hasta que
   acaba la ventana. Sin tiempo de reintento fuera de su schedule.

Usuario: jugador de Whiteout Survival con varias instancias. Exito = no
abrir/cerrar de mas y respetar timers; fallo = reintentar pronto.

## Tech Stack

Python >=3.8, OpenCV template matching, Tesseract OCR (pytesseract),
Tkinter GUI, pytest / ruff / mypy. Windows-only, emuladores MuMu /
BlueStacks / LDPlayer.

## Commands

```powershell
.venv/Scripts/pip install -e ".[dev]"
python main.py
.venv/Scripts/ruff check .
.venv/Scripts/ruff format .
.venv/Scripts/mypy src/wosutil/
.venv/Scripts/python -m pytest
.venv/Scripts/python -m pytest tests/test_utils.py
```

Full check requerido tras cambios: ruff + mypy + pytest.

## Project Structure

```text
main.py                    -> entry point GUI
src/wosutil/tool/tasks/
  task_definitions.py      -> reschedule/retry/early/late por tarea
  task_automation.py       -> cada tarea devuelve bool o (bool, segundos)
  task_schedule.py         -> persistencia next_run_time + last_result
  task_helpers.py          -> navegacion, intel, bear rallies
src/wosutil/tool/
  tool_instances_controller.py -> pick_scheduled_task + loop + reschedule
  utc_time.py              -> reloj UTC y schedule Bear Hunt del task-list
tests/                     -> pytest
data/*.json                -> gitignored, auto-creado en runtime
SPEC-retry-flex.md         -> este documento
```

## Code Style

Definiciones con los 4 tiempos explicitos (segundos). Ejemplo:

```python
"claim_idle": {
    "id": "claim_idle",
    "name": "Claim Idle Income",
    "function": claim_idle_income,
    "priority": 4,
    "reschedule_seconds": 8 * 60 * 60,  # exito sin timer
    "retry_seconds": 2 * 60 * 60,       # error
    "early_seconds": 2 * 60 * 60,       # se puede adelantar 2h
    "late_seconds": 1 * 60 * 60,        # o retrasar 1h
    "category": "exploration",
},
```

Convenciones: Google docstrings, ruff line-length 200, doble comilla,
`log_message` con `level`, sin comentarios largos (el por que va aqui,
no en el codigo).

## Comportamiento por tarea (acordado)

`S` = success default, `E` = retry error, `T` = timer/UTC exacto.

| id | S | E | T / notas |
|---|---|---|---|
| `play_bear_trap` | schedule task-list | SIN retry | Prep 5min + ventana 30min. Al acabar ventana re-lee task-list. Sin schedule conocido: 6h para reintentar lectura. Fallo dentro de ventana: recuperar y seguir, no abortar. |
| `claim_idle` | 8h | 2h | Sin timer. early 2h / late 1h |
| `donate_tech` | 4h | 2h | Sin timer. early 1h / late 0 |
| `autojoin` | 7h | 2h | Sin timer. early 2h / late 30min |
| `claim_island` | 8h | 2h | Sin timer. early 2h / late 1h |
| `claim_mail` | 8h | 2h | Sin timer. early 2h / late 1h |
| `claim_alliance_chests` | 10h | 2h | Sin timer. early 2h / late 2h |
| `claim_triumph` | 12h | 2h | Sin timer. early 2h / late 0 |
| `claim_recruit_hero_free_chest` | 5h si exito sin timer | 2h | Timer `recruit_free_chest_timer` si se lee. |
| `claim_storehouse_stamina` | 12h si exito sin timer | 2h | Timer `storehouse_claim_stamina_timer` si se lee. early 0 / late 4h |
| `do_intel_missions` | 6h si exito sin timer | 2h | Timers: round-trip marcha bestia + `intel_timer`/`intel_timer2` si >=60s. Error = no se pudo ni abrir intel (`ensure_intel_screen` falla). early 0 / late 2h |
| `claim_nomadic_shop_rss_and_vip` | 12h fallback si exito sin reloj | 2h | Exito: hasta 00:00 UTC. early 0 / late 4h |
| `claim_mystery_shop` | 12h fallback si exito sin reloj | 2h | Exito: hasta 00:00 UTC. early 0 / late 4h (sube de 10h a 12h) |
| `claim_vip_daily_rewards` | 12h fallback si exito sin reloj | 2h | Exito: hasta 00:00 UTC. early 0 / late 4h |
| `claim_tundra_trek_supplies` | 6h si exito sin timer | 2h | Timer `tundra_trek_supplies_timer` si <=16h. early 0 / late 2h |
| `start_tundra_trek_idle` | tras 15 (`run_after`) | 2h (mantiene `run_after`) | Bool puro. Siempre despues de 15. |
| `claim_pet_adventure_ally_treasure` | 12h fallback si exito sin reloj | 2h | Exito: hasta 00:00 UTC. early 0 / late 3h |
| `send_pet_adventure_chests` | 5h normal / UTC-midnight si sin intentos (fallback 6h) | 2h (antes 5h) | early 0 / late 0 |
| `activate_daily_pet_skills` | 6h si exito sin timers | 2h | Exactos: `march_walking_time` del gather ox o minimo de los 4 `pet_skill_*_timer`. early 0 / late 10min |
| `train_troops` | 6h si exito sin timers | 2h | Exacto: minimo de los 3 timers de campamento. early 0 / late 10min |

Cambios respecto a hoy: `claim_idle` 7h->8h, `claim_island` 7h->8h,
`nomadic/mystery` fallback 10h->12h, `recruit` exito-sin-timer 2h->5h,
`storehouse` exito-sin-timer 4h->12h, `intel` exito-sin-timer 4h->6h,
`pet_chests` error 5h->2h. Se elimina el fallback 2 dias de bear trap.

## Reglas de scheduling

1. Retorno: `True/False` o `(bool, segundos>0)`. Tupla valida sobrescribe
   solo ese ciclo, no el default base.
2. Exito con timer/UTC: `due = now + segundos_exactos`.
3. Exito sin timer: `due = max(nominal_due, now) + success`. Ancla al `due`
   nominal para no encadenar derivas (adelanto no adelanta el siguiente,
   retraso puntual no se acumula).
4. Error: `due = now + retry_seconds`, `last_result=error`, sin flex en ese
   ciclo (`early=late=0` efectivos). Bear trap no tiene `retry_seconds`.
5. Flex solo si `last_result != error`:
   - Ejecutable-ahora si `now >= due - early`.
   - Si instancia abierta: correr todo lo ejecutable en orden de prioridad
     (early-batching).
   - Si instancia cerrada: antes de abrir por R, mirar F futura con
     `due_F` en `(now, due_R + late_R]` no adelantable
     (`due_F - now > early_F`). Si existe, quedarse cerrado hasta `due_F`
     y hacer R+F juntas (delayed-open). Nunca pasar de `due_R + late_R`.
   - Umbral 120s actual para cerrar vs esperar abierto se mantiene: si el
     batch conjunto es mas lejos, cerrar y reabrir una vez.
6. `run_after` (`start_tundra_trek_idle` tras `claim_tundra_trek_supplies`)
   se mantiene y fuerza `now`.
7. Persistencia (`task_schedule.json`): guardar `next_run_time` (= due
   nominal), `reschedule_seconds` base y `last_result`. Un retry a 2h no
   debe volverse ejecutable al instante por el early de otra tarea.
8. Bear trap dentro de ventana: cualquier fallo de pantalla/juego intenta
   `ensure_world_screen` / relanzar y continua mientras `now < fin`.
   Solo al acabar la ventana se re-lee el task-list. Prioridad maxima,
   ocupa la instancia toda la ventana.

## Testing Strategy

Framework pytest en `tests/`, `testpaths=["tests"]`.

- Unit: `build_task_state` / `snapshot_instance_schedule` con `last_result`;
  `pick_scheduled_task` con early (adelanta), con error (no adelanta),
  delayed-open (`idle` 1h + `train` 2h -> una apertura a las 2h),
  limite `due+late` nunca superado, ancla `max(nominal, now)+success`.
- Unit: bear trap `next_bear_hunt_start` + reschedule tras ventana +
  loop que continua dentro de ventana ante fallo (mock tiempo/pantalla).
- Unit: intel distingue "sin misiones" (True) de "sin pantalla" (False).
- Contrato: toda tarea con timer devuelve tupla; fallbacks y errores con
  los segundos de la tabla.
- Full check: ruff + mypy `src/wosutil/` + pytest antes de push (CI igual).

## Boundaries

- Always: correr full check tras cambios; no cambiar `due` nominal en
  errores salvo `now+retry`; respetar `run_after`; no ejecutar flex en
  ciclos de error; bump `version` en `pyproject.toml` en la PR de cambio.
- Ask first: cambiar umbral 120s, cambiar prioridades, anadir dependencias,
  tocar CI/build, cambiar formato de `data/*.json` sin migracion.
- Never: commitear secretos o `data/`; editar `templates/` binarios sin
  necesidad; borrar tests que fallen sin aprobacion; reintentos sin tope
  dentro de bear trap (siempre acotado a fin de ventana).

## Success Criteria

- [ ] Fallar `claim_idle` reprograma ~2h, completarla ~8h (igual resto de
  la tabla con sus valores).
- [ ] Exito con timer usa el timer; exito sin timer usa el fallback de la
  tabla; UTC usa medianoche o fallback 12h.
- [ ] Bear trap sin schedule no programa 2 dias; dentro de ventana un
  cierre del juego se recupera y sigue hasta fin; tras ventana re-lee.
- [ ] `idle` (due 1h) + `train` (due 2h, no adelantable) con instancia
  cerrada abren una sola vez a las 2h; un error a 2h no se adelanta por
  early ajeno.
- [ ] Carreras repetidas pronto/tarde no derivan: `due` siguiente anclado
  a nominal.
- [ ] `ruff check .`, `mypy src/wosutil/`, `pytest` en verde.

## Open Questions (cerradas con el usuario)

- 8 `triumph` late = 0. Hecho.
- 2/5/6 comparten -2h/+1h. Hecho.
- 20 fallback 6h. Hecho.
- Bear trap en fallo sin re-lectura extra: usa cache conocido, 6h solo si
  no hay schedule. Pendiente de validar en review de este SPEC.
- Intel error = no abrir pantalla. Hecho.
