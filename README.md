# FlowAgent

Automatización visual de flujos de trabajo con IA sobre Frappe: builder drag-and-drop con agentes de IA, disparadores por DocType y orquestación multi-paso.

> Repo derivado de `MirzaAreebBaig/Frappe-FlowAgent` (upstream). Este repo es el fork de trabajo de Grupo224, rama `feat/multiprovider`.

## Stack

- **Backend:** Python 3.12, Frappe **v15**, app `flowagent` (v0.5.7).
- **LLM:** multi-proveedor — Anthropic, OpenAI, DeepSeek, OpenRouter, Ollama.
- **Entrada en desk:** hook `add_to_apps_screen` (aparece como "FlowAgent").

## Multi-proveedor LLM (rama `feat/multiprovider`)

El motor de LLM (`flowagent/engine/llm.py`) expone un `UnifiedClient` con la misma superficie que el SDK de Anthropic (`client.messages.create(...)`), así los nodos existentes no cambian.

- `PROVIDERS = ['Anthropic', 'OpenAI', 'DeepSeek', 'OpenRouter', 'Ollama']`.
- OpenAI-compatibles (DeepSeek / Ollama / OpenAI / OpenRouter) van por `requests` (sin dependencia nueva).
- Defaults de modelo por proveedor (`DEFAULT_MODELS`): Anthropic→`claude-sonnet-4-5`, OpenAI→`gpt-4o`, DeepSeek→`deepseek-chat`, OpenRouter→`openai/gpt-4o`, Ollama→`llama3.1`.
- `get_default_model()` resuelve el modelo según `default_provider`.
- Las API keys se guardan en campos **Password** del child table `FlowAgent Provider`.

## Estructura

- `flowagent/engine/` — `runner.py` (ejecución), `nodes/` (AI, logic), `llm.py` (multi-proveedor), `triggers/`.
- `flowagent/api/` — `studio.py` (guardar/editar workflows), `ai_build.py`, `engineer.py` (loop diseño→test), `approval.py`, `webhook.py`, `forms.py`.
- `flowagent/flowagent_core/` — doctypes core (Workflow, Workflow Run, Provider, Settings, etc.) y el Workspace "FlowAgent".

## Despliegue

### Requisitos
- Frappe **v15** (no v16).
- Python 3.12.

### Instalar

```bash
cd ~/frappe-bench
bench get-app git@github.com:Grupo224/FLOW-ALTOPLANO.git --branch feat/multiprovider
bench --site <sitio> install-app flowagent
bench --site <sitio> migrate
```

### Configurar un proveedor LLM

1. Desk → FlowAgent → **FlowAgent Settings**.
2. Elegir `default_provider` (ej. DeepSeek).
3. Agregar una fila en `providers` con la API key (campo Password) y `base_url`.
4. Botón **Test Connection** para validar la key sin ejecutar un flujo completo.

## ⚠️ Bugs / temas detectados (para revisar)

1. **`default_model` desactualizado en UI** — el campo sigue mostrando `claude-sonnet-4-5` aunque `default_provider` sea `DeepSeek`. No rompe nada (`get_default_model()` resuelve correcto), pero conviene limpiar ese campo.
2. **API key de DeepSeek sin configurar** — solo se recibió enmascarada; hay que guardar la key real en FlowAgent Settings → providers.
3. **`flow_client` BLOQUEADO** — requiere Python ≥3.14,<3.15 y Frappe 16; este servidor tiene Python 3.12 y Frappe 15. No instalar en este bench.
4. **Los disparadores DocType requieren reinicio del worker** — `hooks.py` arma `doc_events` al arrancar el worker; al agregar un workflow con trigger sobre un DocType nuevo, reiniciar (`bench restart` / `systemctl --user restart erpnext-bench`).
5. **Logs del Engineer dicen "Calling Anthropic API"** aunque use DeepSeek — es solo texto de log (`engineer.py`), no afecta el proveedor real. Conviene renombrar el texto.

## TODO

- [ ] Limpiar `default_model` para que refleje el proveedor activo.
- [ ] Renombrar los logs "Anthropic" → proveedor genérico.
- [ ] Subir la rama al remoto (ver README de despliegue).

## Notas de desarrollo (gotchas ya resueltos)

- **Engineer loop** (`engineer.py`): `frappe.enqueue` reserva `job_id` como kwarg propio — usar otro nombre (`engineering_job_id`).
- **Thread sin contexto frappe**: un `ThreadPoolExecutor` no tiene `frappe.local`; inicializar con `frappe.init(site=…, sites_path=…)`, `frappe.connect()`, `frappe.set_user(user)` dentro del hilo.
- **Visibilidad cross-thread de DB**: el `commit()` del hilo no lo ve el hilo llamador hasta cerrar su transacción; construir el resultado dentro del hilo y devolverlo.
- **Condiciones de `logic_condition`**: el modelo genera Jinja (`{{ var }}`, `| length`, `=`); `_normalize_condition()` lo normaliza antes de `ast.parse`.
