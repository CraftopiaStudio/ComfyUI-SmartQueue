# Analyserapport ComfyUI-SmartQueue 0.1.6

Datum: 2026-09-09. Scope: volledige codebase op branch `claude/comfyui-node-runtime-analysis-1h98g3` (commit `a7c45e1`). Er is niets aan de code gewijzigd; dit document is het enige dat is toegevoegd.

Geverifieerd tegen:

- ComfyUI `master` (september 2026): `server.py`, `execution.py`, `nodes.py`, `main.py`, `folder_paths.py`, `comfy/model_management.py`, `comfy_execution/utils.py`, `comfy_api/latest/_io.py`.
- ComfyUI tags `v0.0.1`, `v0.2.0`, `v0.3.0`, `v0.3.30`, `v0.3.50`, `v0.3.60`, `v0.4.0` t/m `v0.12.0` (voor de `last_prompt_id`-vraag).
- `comfyui_manager==4.2.2` (de versie die ComfyUI via `manager_requirements.txt` pint).
- ComfyUI_frontend `main`: `src/scripts/api.ts`, `src/services/extensionService.ts`.
- Comfy Registry `registry/standards.mdx` (docs-repo). De site `docs.comfy.org` zelf was vanuit deze omgeving niet bereikbaar.
- Lokale testrun: `pytest tests/` → 204 passed (README zegt 214).

Het bestand `registry_status_last.txt` staat in `.gitignore` en zit dus niet in de checkout. De ban-tekst waar dit rapport op leunt is de letterlijke policy-quote uit `CHANGELOG.md` (0.1.6): *"attacker-reachable via unauthenticated /prompt (node widget) or no-auth route"*, plus het feit dat 0.1.2 en 0.1.4 `Banned` en 0.1.2/0.1.3 `Flagged` terugkwamen.

---

## 1. Samenvatting en prioriteit

| # | Ernst | Bevinding | Gevolg in de praktijk |
|---|---|---|---|
| 1 | Kritiek | `SmartCooldownNode.execute` leest `PromptServer.instance.last_prompt_id`. Dat attribuut bestaat in geen enkele ComfyUI-versie (v0.0.1 t/m master) en ook niet in ComfyUI-Manager. | Zodra `wait_for_click` aan staat: `AttributeError` → "Exception during processing", de run faalt. Alle wait/Continue/Cancel/pending_waits-iteraties zijn daarmee dood op een schone install. |
| 2 | Kritiek | `queue_middleware` matcht alleen `request.path == "/prompt"`. De frontend post naar `/api/prompt` (ComfyUI dupliceert elke route met `/api`-prefix). | De 423-poort werkt niet met de ingebouwde frontend. Op **Run** klikken tijdens een (manual of autopilot) pauze rendert direct. Alleen jobs die al in de queue stonden op het moment van pauzeren worden vastgehouden. |
| 3 | Hoog (registry) | `gpu_monitor.poll_gpu_metrics` spawnt `nvidia-smi` via `subprocess.run`, en wordt aangeroepen vanuit de node (`wait_for_temp`, standaard **aan**). Dat is letterlijk "process spawn reachable via unauthenticated /prompt (node widget)". | 0.1.6 verwijderde alleen de route-variant (browse_sound_file). De node-variant staat er nog. Kans op opnieuw `Banned` bij de volgende publish is reëel. |
| 4 | Middel | `web/tests/test_format_duration.js` wordt door ComfyUI als frontend-extensie geladen (`/extensions` globt `**/*.js` recursief). | `import "node:test"` faalt in de browser: "Error loading extension" in de console bij elke pagina-load. |
| 5 | Middel | `save_held_items` schrijft het volledige queue-tuple naar SQLite, inclusief element 5 (`sensitive`: `auth_token_comfy_org`, `api_key_comfy_org`). | Comfy.org-tokens op schijf in platte tekst. Registry-reviewers en scanners zien "credentials persisted". |
| 6 | Middel | ComfyUI core heeft nu een runtime-blokkade: `nodes.py` slaat een pack over als `comfyui_manager.should_be_disabled()` waar is ("Blocked by policy"). In Manager 4.2.2 treft dat alleen legacy-Manager-mappen, maar de docstring zegt dat de blocklist per policy uitgebreid wordt. | Vandaag nog niet jouw probleem. Wel de plek waar een registry-ban in de toekomst een echte runtime-disable wordt. |
| 7 | Laag | Diverse registratie- en robuustheidspunten (§3, §4). | Geen blokkade, wel afwerking. |

---

## 2. Runtime-blokkades: oorzaak en oplossing (doel 1)

### 2.1 Comfy Registry ban onder policy-v0.2

**Hoe de registry werkt (twee lagen).** Laag 1 is een patroon-scanner (YARA-achtige regels): die levert `NodeVersionStatusFlagged` en is wat je in `SECURITY.md` al als false positives documenteert (`sqlite3.connect`, `os.environ`, `urllib`, `.bind(`). Laag 2 is de policy-check: elke plek waar een proces gestart wordt of code geëvalueerd wordt, en die bereikbaar is via `/prompt` (een node-widget) of via een route zonder auth, levert `NodeVersionStatusBanned`. Een ban geldt per versie: ComfyUI-Manager en comfy-cli weigeren die versie te installeren of te updaten. Een git-clone blijft werken.

**Waarom 0.1.6 nog steeds in de gevarenzone zit.** De keten is:

```
POST /prompt (geen auth)
  → SmartCooldownNode.execute()            backend/nodes/cooldown.py:186-198
  → run_cooldown(wait_for_temp=True, ...)  standaard True in het schema
  → metrics_fn = poll_gpu_metrics
  → subprocess.run(["nvidia-smi", ...])    backend/gpu_monitor.py:47
```

Dat de argumentlijst constant is, maakt voor de scanner niet uit. De policy-quote gaat over **bereikbaarheid**, niet over injecteerbaarheid. Jouw eigen 0.1.6-changelog trekt die conclusie al voor de route-variant ("it goes rather than gets defended") maar laat de node-variant staan. De patroon-laag ziet daarnaast nog: `import subprocess`, `os.environ.get`, `sqlite3.connect`, `conn.executescript`, twee f-string SQL-statements, `shutil.copy2`, `urllib`.

**Oplossing (stapsgewijs).**

1. Vervang de subprocess-aanroep door NVML via `nvidia-ml-py` (module `pynvml`). Dat is een ctypes-binding op `libnvidia-ml`, dus geen proces, geen shell. Zet in `pyproject.toml`: `dependencies = ["nvidia-ml-py"]`. Dat is precies de "centralized dependency management" die de registry-standaard wil.
2. Houd `GpuMetrics` en de functienaam `poll_gpu_metrics()` intact zodat `__init__.py`, `cooldown.py`, `autopilot_loop.py` en alle tests ongewijzigd blijven. Schets:

   ```python
   def poll_gpu_metrics() -> GpuMetrics:
       try:
           import pynvml
           pynvml.nvmlInit()
           try:
               handle = _target_handle(pynvml)  # zie stap 3
               temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
               mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
               return GpuMetrics(float(temp), mem.used / 2**20, mem.total / 2**20)
           finally:
               pynvml.nvmlShutdown()
       except Exception:
           return _EMPTY
   ```

   `nvmlInit`/`nvmlShutdown` mogen per tick; het is goedkoop. Wil je het netter, init één keer bij laden en cache de handle.
3. GPU-selectie zonder `os.environ`: NVML nummert op PCI-busvolgorde, CUDA op `CUDA_VISIBLE_DEVICES`. Betrouwbaarder dan de env-var lezen is het UUID-pad: `torch.cuda.get_device_properties(comfy.model_management.get_torch_device()).uuid` → `pynvml.nvmlDeviceGetHandleByUUID(f"GPU-{uuid}")`. Val terug op index 0 als dat faalt. Dan verdwijnt ook de `python_environment_manipulation`-finding.
4. VRAM-fallback zonder NVML: `comfy.model_management.get_free_memory()` en `get_total_memory()` geven dezelfde getallen die ComfyUI zelf gebruikt. Temperatuur blijft dan `None`, wat je fail-open pad al afhandelt.
5. Verwijder `import subprocess` en `import os` volledig uit het pakket. Doel: `grep -rn "subprocess\|os.environ" backend/` levert nul hits.
6. Verwijder de f-string SQL uit `_migrate_schema`: schrijf de twee `ALTER TABLE` en `PRAGMA table_info` statements als letterlijke, volledig uitgeschreven strings per kolom. Er zijn er maar drie. Vervang `executescript` door losse `execute`-aanroepen per `CREATE TABLE`.
7. Herschrijf `SECURITY.md`: het codeblok met `cmd = ["nvidia-smi", ...]` wordt overbodig en de bewering "SQLite inside the extension's own directory" klopt niet meer (het staat in `user/__smart_queue/`, zie `db_location.py`).
8. Publiceer, en controleer daarna de versiestatus via `GET https://api.comfy.org/nodes/comfyui-smartqueue/versions` (je bestaande `check_registry_status`-script). `Flagged` is installeerbaar; `Banned` niet.

### 2.2 Runtime-blokkade in ComfyUI core zelf ("Blocked by policy")

`nodes.py` (master), in de laadlus van `custom_nodes`:

```python
if args.enable_manager:
    if comfyui_manager.should_be_disabled(module_path):
        logging.info(f"Blocked by policy: {module_path}")
        continue
```

`comfyui_manager/__init__.py` 4.2.2 implementeert dat nu als: mapnaam bevat `comfyui-manager` → overslaan. Docstring: *"The blocklist can be expanded later based on policies."* Manager's `create_middleware()` bant alleen handlers met een `HANDLER_POLICY`-markering (Manager's eigen routes); jouw `/smart_queue/*`-routes raakt het niet.

**Conclusie:** SmartQueue wordt vandaag niet door de runtime geblokkeerd. De plek waar dat kan gaan gebeuren is deze hook, gevoed door registry-status. §2.1 oplossen is dus ook de preventie hiervoor.

### 2.3 `wait_for_click` faalt: `last_prompt_id` bestaat niet

`backend/nodes/cooldown.py:229`:

```python
prompt_id = PromptServer.instance.last_prompt_id
```

`PromptServer` heeft `client_id` en `last_node_id` (server.py:264, execution.py:495), geen `last_prompt_id`. Dat geldt voor elke tag die ik heb gecontroleerd en voor `comfyui_manager` 4.2.2. Als het bij jou live ooit werkte, kwam dat attribuut van een ander geïnstalleerd pack dat `PromptServer.instance` monkey-patcht, niet van ComfyUI. Op een schone install: `AttributeError` binnen `execute()` → ComfyUI logt "!!! Exception during processing" en de prompt faalt. Omdat `send_sync("smart_queue.cooldown_wait_for_click")` vóór de fout wordt gestuurd, gaat de node in de UI wél knipperen, terwijl er server-side niets wacht: `wait_for_continue` is nooit bereikt. De 3-seconden-reconcile in `smart_queue_node.js` zet de knipper daarna weer uit omdat `/smart_queue/pending_waits` leeg is. Dat is de "knippert even en stopt"-symptomatiek.

**Oplossing.**

1. Gebruik de officiële executie-context. ComfyUI zet die rond elke node-aanroep (`execution.py:294,305`):

   ```python
   from comfy_execution.utils import get_executing_context

   ctx = get_executing_context()
   prompt_id = ctx.prompt_id if ctx is not None else None
   ```

2. Fallback voor oudere ComfyUI zonder `comfy_execution.utils`: de worker draait één prompt tegelijk, dus

   ```python
   running, _ = PromptServer.instance.prompt_queue.get_current_queue_volatile()
   prompt_id = running[0][1] if running else None
   ```

3. Fail-open: is `prompt_id` nog steeds `None`, sla de wait over en zet dat in `status` ("wait_for_click skipped: no prompt id"), in lijn met de rest van je architectuur.
4. Voeg een test toe die `cooldown.py` parseert en faalt op de string `last_prompt_id` (zelfde techniek als `test_cooldown_schema_order.py`), zodat dit niet terugkomt.
5. Overweeg voor de Cancel-knop `prompt_queue.interrupt_if_running(prompt_id)` (nieuw in core, execution.py:1323). Dat zet de globale interrupt-vlag die `wait_for_continue` al pollt via `throw_exception_if_processing_interrupted()`. Dan kan `signal_cancel` en de route `POST /smart_queue/cancel_wait/{prompt_id}` weg: één route minder voor de scanner.

### 2.4 Pauze blokkeert nieuwe submissions niet (`/api/prompt`)

`server.py:1233-1240` registreert elke `RouteDef` tweemaal: als `/prompt` en als `/api/prompt`. De frontend gebruikt uitsluitend de tweede (`api.ts:498-500`: `apiURL()` plakt `/api` voor elke route; `queuePrompt` → `fetchApi('/prompt')`). Jouw middleware (`queue_middleware.py:13`) test `request.path == "/prompt"` en ziet dus nooit een submission van de ingebouwde UI. `tests/test_queue_middleware.py` test alleen het kale pad en kan dit niet vangen.

Gevolg: manual pause houdt de bestaande queue vast (via `QueueHold`, dat is een edge-trigger op de overgang), maar alles wat je daarna queuet loopt er direct doorheen. Datzelfde geldt voor autopilot-pauzes: de job-count-, temp- en VRAM-regels gaten niets wat na de overgang binnenkomt.

**Oplossing.**

1. Minimale fix: `request.path in ("/prompt", "/api/prompt")`, of `request.path.removeprefix("/api") == "/prompt"`. Voeg een testcase voor `/api/prompt` toe.
2. Architectonisch (aanbevolen naast 1): maak de hold idempotent per tick. In `sync_queue_hold` niet alleen op de overgang `held` handelen, maar zolang `state.effective_paused` waar is elke tick `queue_hold.hold_pending(prompt_queue)` aanroepen en de nieuwe items aan `held_items` toevoegen. Dan worden ook submissions via API-clients, MCP of een frontend die de 423 negeert netjes vastgehouden in plaats van uitgevoerd. De 423 blijft dan puur UX-feedback.

### 2.5 `web/tests/` wordt als frontend-extensie geladen

`server.py:364`: voor elke `WEB_DIRECTORY` globt ComfyUI `**/*.js` recursief en de frontend importeert elk bestand (`extensionService.ts:62-64`, met per-bestand `console.error('Error loading extension', ...)`). `web/tests/test_format_duration.js` importeert `node:test` en `node:assert/strict`; dat resolveert niet in een browser. Geen crash, wel een rode fout bij elke pagina-load, en scanners lezen ook dit bestand mee.

**Oplossing.** Verplaats `web/tests/` naar bijvoorbeeld `tests_web/` (buiten `web/`), pas `.github/workflows/ci.yml` regel 26 en de README aan. `format_duration.js` mag blijven: het is een puur ES-module zonder side effects. Alternatief is de test hernoemen naar `.mjs` (de glob pakt alleen `*.js`), maar verplaatsen is duidelijker.

### 2.6 Gevoelige data in `held_items`

`persistence.save_held_items` doet `json.dumps(list(item))` over het volledige queue-tuple. Element 5 is `sensitive`, dat `server.py:1125-1129` bewust uit `extra_data` haalt (`SENSITIVE_EXTRA_DATA_KEYS = ("auth_token_comfy_org", "api_key_comfy_org")`) zodat het nooit in history of logs terechtkomt. Jij schrijft het naar `smart_queue.sqlite3`.

**Oplossing.** Sla `item[:5]` op en vul bij `load_held_items` een lege dict aan als zesde element. Een na een herstart vrijgegeven job verliest dan zijn API-node-token; dat is correct gedrag (de gebruiker logt opnieuw in). Documenteer het in `SECURITY.md` onder "Filesystem".

---

## 3. Klasse-registratie (doel 2)

Je gebruikt het V3-schema (`io.ComfyNode` + `define_schema`) en registreert via de V1-mappings. Dat is toegestaan: `nodes.py:2295-2301` neemt elke klasse uit `NODE_CLASS_MAPPINGS`, en `server.py:751-754` detecteert `_ComfyNodeInternal` en gebruikt `GET_NODE_INFO_V1()`. `INPUT_TYPES`, `RETURN_TYPES`, `FUNCTION` en `OUTPUT_NODE` worden door `GET_SCHEMA()` gegenereerd; die mag je dus **niet** zelf definiëren. Er is geen fout in de registratie die maakt dat de node niet verschijnt. Wel de volgende punten.

| Onderdeel | Status | Opmerking |
|---|---|---|
| `NODE_CLASS_MAPPINGS` / `NODE_DISPLAY_NAME_MAPPINGS` / `WEB_DIRECTORY` / `__all__` | OK | Namen consistent met `node_id` en `display_name` in het schema. |
| `io.Schema(node_id, display_name, category, inputs, outputs, hidden, is_output_node)` | OK | Alle velden bestaan in de `Schema`-dataclass (`_io.py:1702-1739`). |
| `io.Float.Input(..., step=)` / `io.Boolean.Input(..., display_name=)` / `io.Combo.Input(id, options=, default=)` / `io.String.Input(default=)` | OK | Signatures kloppen met `_io.py:253-404`. |
| `io.AnyType.Input(optional=True)` en `io.AnyType.Output` | OK | |
| `hidden=[io.Hidden.unique_id]` → `cls.hidden.unique_id` | OK | `HiddenHolder` (`_io.py:1546-1551`) wordt via `PREPARE_CLASS_CLONE` per uitvoering gezet. |
| `execute` als `@classmethod`, `**kwargs` per input-id, `io.NodeOutput(...)` | OK | |
| `_NodeBase = io.ComfyNode if _HAS_COMFY_IO else object` | Let op | Op een ComfyUI zonder `comfy_api` registreer je een kale `object`-subklasse. `get_object_info` vangt dat per node af (`server.py:806-810`) en logt een error; de node ontbreekt dan stil in de UI. Beter: registreer in dat geval niets (`NODE_CLASS_MAPPINGS = {}` met een duidelijke `logger.error`), of eis `comfy_api` hard. |
| Import-time side effects in `__init__.py` (DB openen, `middlewares.append`, held items laden) | Let op | Faalt één ervan (read-only `user/`-map, gewijzigde `PromptQueue`-API, app al gefreezed), dan faalt de hele package-import en is ook de node weg. Zet alles na de mappings in een `try/except` met logging, zodat de node altijd registreert en alleen autopilot uitvalt. Dat is de "fail-open" die je README belooft. |
| `app.middlewares.append` na constructie | Werkt nu | aiohttp bevriest de lijst pas in `runner.setup()`, na het laden van custom nodes. Er is geen publieke hook voor; houd dit, maar wel binnen de `try/except` hierboven. |
| Directe `app.router.add_get/post("/smart_queue/...")` | Let op | Alleen het kale pad; geen `/api/smart_queue/...`-variant. De JS gebruikt kale paden, dus consistent, maar achter een reverse proxy of de frontend-devserver (die alleen `/api` doorstuurt) zijn je routes onbereikbaar. Registreer beide, of gebruik `PromptServer.instance.routes` (dan maakt core de `/api`-kopie zelf). |
| Synchrone `execute` met `time.sleep` en `event.wait` | Architectuur | V3 ondersteunt `async def execute`. Je blokkeert nu de event loop die `execute_async` voor de hele prompt draait; met `await asyncio.sleep()` en een `asyncio.Event` blijft de executor responsief en wordt cancel/interrupt schoner. Niet verplicht, wel de richting waar core heen gaat. |
| `run_cooldown(clock_fn=...)` | Cosmetisch | Parameter wordt geaccepteerd maar nergens gebruikt; `elapsed` telt `poll_interval_seconds` op in plaats van de klok te lezen. Werkt, maar `max_wait_seconds` wijkt af zodra `nvidia-smi` traag is. |
| README testcount | Cosmetisch | README zegt 214, `pytest` telt 204. |

---

## 4. Overige bevindingen (geen blokkade)

- **`InterruptProcessingException` is nu een `BaseException`** (`model_management.py:2133`). Jouw pytest-fallback in `continue_registry.py` erft van `Exception`. Alleen relevant voor tests, maar maak de fallback gelijk aan core zodat `except Exception:`-paden in tests hetzelfde gedrag tonen.
- **`get_history()` zonder argumenten** haalt elke 5 s de volledige history-dict (tot `MAXIMUM_HISTORY_SIZE`) op en itereert erover. Gebruik `get_history(max_items=N)` met een kleine N; `seen_completed` beperkt zich toch al tot wat core nog vasthoudt.
- **`_maybe_free_vram_on_pause_tick`** roept `unload_all_models()` aan vanaf de aiohttp-thread. Door §2.4 kan er tussen de `running`-check en de unload een job starten. Na de fix van §2.4 is dat risico klein; zet de aanroep desnoods achter `prompt_queue.mutex`.
- **`.comfyignore`** sluit `docs/` en `.github/` uit maar niet `web/tests/`. Na §2.5 vanzelf opgelost.

---

## 5. Aanbevolen volgorde

1. §2.3 (`last_prompt_id`) en §2.4 (`/api/prompt`): twee kleine fixes, herstellen de twee kernfuncties.
2. §2.1 (`nvidia-smi` → NVML) en §2.6 (`sensitive` niet persisteren): dit is de publish-blocker.
3. §2.5 (`web/tests` verplaatsen) en de `try/except`-hardening uit §3.
4. `SECURITY.md`, README en CHANGELOG bijwerken; versie naar 0.1.7; publiceren en de registry-status checken.
