# 🎓 KekeTranslate

Transcripción y **anotación inteligente de clases largas** (2–4 horas). Sube la
grabación de una clase y recupera dos cosas: la transcripción completa con
identificación de oradores, y unos apuntes estructurados en Markdown al estilo
del *Anotador de reuniones* de Notion — título, resumen ejecutivo, temas clave,
notas cronológicas detalladas, tareas pendientes y glosario.

---

## Por qué este stack

El requisito que condiciona todo el diseño es la **duración**: una clase de 4
horas son entre 200 MB y 2 GB de audio, y ninguna API de transcripción síncrona
sirve para eso. Las decisiones se tomaron a partir de los límites reales de cada
proveedor:

| Proveedor | Tamaño máximo | Duración máxima | Diarización | ¿Hace falta segmentar? |
|---|---|---|---|---|
| **AssemblyAI** *(por defecto)* | 5 GB (2,2 GB vía `/v2/upload`) | **10 h** | ✅ nativa | ❌ **No** |
| Deepgram `nova-3` | ~2 GB | larga | ✅ nativa | ❌ No |
| OpenAI Whisper / `gpt-4o-transcribe` | **25 MB** | — | ❌ No | ✅ Sí, obligatorio |

**AssemblyAI es el proveedor por defecto** porque una clase de 4 h entra completa
en una sola petición: nada de cortar el audio, nada de pegar transcripciones con
las costuras a la vista, nada de perder la continuidad de los oradores entre
fragmentos. El trabajo se encola de forma asíncrona y KekeTranslate sondea su
estado.

Los otros dos siguen disponibles cambiando una variable de entorno. El proveedor
`openai` es el caso interesante: como su tope de **25 MB por petición** hace
imposible enviar una clase entera, KekeTranslate segmenta el audio con `ffmpeg`
en bloques de 10 minutos (MP3 mono a 64 kbps, ~4,8 MB cada uno), los transcribe
por separado y recompone el resultado **desplazando las marcas de tiempo** de
cada bloque. Aun así no ofrece identificación de oradores, así que solo conviene
como alternativa.

Para los apuntes se usa **Claude Opus 5** (`claude-opus-5`). Su ventana de
contexto de **1 millón de tokens** es la razón: la transcripción de una clase de
4 horas ronda los 50–70k tokens, así que el modelo la lee **entera de una vez** y
puede relacionar algo que se dijo en el minuto 12 con lo que se retomó en el
minuto 200. Esa visión global es justo lo que un enfoque troceado pierde.

| Capa | Tecnología | Motivo |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Subidas en streaming a disco y trabajos en segundo plano |
| Transcripción | AssemblyAI · Deepgram · OpenAI | Intercambiables desde el `.env` |
| Anotador IA | Claude Opus 5 | 1M de contexto, *adaptive thinking*, streaming y *prompt caching* |
| Frontend | Streamlit | Interfaz de subida y lectura sin paso de compilación |
| Estado | SQLite | Un trabajo de 4 h sobrevive a un reinicio del servidor |

---

## Arquitectura

```
                subida en bloques de 4 MB          trabajo en segundo plano
   Streamlit ──────────────────────────► FastAPI ──────────────────────────┐
       ▲                                    │                              │
       │   sondeo de estado (GET /api/jobs) │                              ▼
       │                                    │                   ┌──────────────────────┐
       │                                    │                   │ 1. Transcripción     │
       │                                    │                   │    AssemblyAI        │
       │                                    │                   │    + diarización     │
       │                                    ▼                   └──────────┬───────────┘
       │                                 SQLite                            │ [HH:MM:SS] Orador A: …
       │                              (estado del job)                     ▼
       │                                                        ┌──────────────────────┐
       └────────────── apuntes .md ◄────────────────────────────│ 2. Anotador IA       │
                                                                │    Claude Opus 5     │
                                                                └──────────────────────┘
```

La transcripción se vuelca a disco **antes** de pasar por el LLM: si la anotación
falla, la parte cara del proceso no se pierde.

```
KekeTranslate/
├── backend/
│   ├── main.py                    # API HTTP (FastAPI)
│   ├── config.py                  # Ajustes desde el entorno
│   ├── models.py                  # Esquemas: Job, Utterance, TranscriptionResult
│   ├── store.py                   # Persistencia en SQLite
│   ├── pipeline.py                # Orquestación audio → transcripción → apuntes
│   ├── media.py                   # ffmpeg: duración y segmentación
│   ├── transcription/
│   │   ├── base.py                # Interfaz común a los proveedores
│   │   ├── assemblyai.py          # Por defecto: 5 GB / 10 h, sin segmentar
│   │   ├── deepgram.py            # nova-3 con diarización
│   │   ├── whisper_openai.py      # Segmentado con ffmpeg (tope de 25 MB)
│   │   └── factory.py
│   └── annotator/
│       ├── claude.py              # Streaming, caching, map-reduce de respaldo
│       └── prompts.py             # Prompts del formato estilo Notion
├── frontend/app.py                # Interfaz Streamlit
└── tests/                         # 24 tests, sin llamadas a APIs externas
```

---

## Instalación

**Requisitos:** Python 3.11 o superior. `ffmpeg` **solo** si vas a usar el
proveedor `openai`.

```bash
git clone https://github.com/AcostaAlex10/KekeTranslate.git
cd KekeTranslate

python3 -m venv .venv
source .venv/bin/activate          # En Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuración

```bash
cp .env.example .env
```

Edita `.env` y rellena, como mínimo, la clave del proveedor de transcripción y la
de Anthropic:

| Variable | Obligatoria | Por defecto | Descripción |
|---|:---:|---|---|
| `TRANSCRIPTION_PROVIDER` | — | `assemblyai` | `assemblyai`, `deepgram` u `openai` |
| `ASSEMBLYAI_API_KEY` | ✅ *(si usas AssemblyAI)* | — | [assemblyai.com](https://www.assemblyai.com/) |
| `DEEPGRAM_API_KEY` | ✅ *(si usas Deepgram)* | — | [deepgram.com](https://deepgram.com/) |
| `OPENAI_API_KEY` | ✅ *(si usas OpenAI)* | — | [platform.openai.com](https://platform.openai.com/) |
| `ANTHROPIC_API_KEY` | ✅ | — | [console.anthropic.com](https://console.anthropic.com/) |
| `ANTHROPIC_MODEL` | — | `claude-opus-5` | Modelo del anotador |
| `ANTHROPIC_EFFORT` | — | `high` | `low`, `medium`, `high`, `xhigh` o `max` |
| `ANTHROPIC_MAX_TOKENS` | — | `32000` | Longitud máxima de los apuntes |
| `TRANSCRIPTION_LANGUAGE` | — | `es` | ISO-639-1. Vacío ⇒ detección automática |
| `ENABLE_DIARIZATION` | — | `true` | Identificación de oradores |
| `EXPECTED_SPEAKERS` | — | *(vacío)* | Número aproximado de oradores; mejora la precisión |
| `STORAGE_DIR` | — | `./storage` | Audios, transcripciones y apuntes |
| `MAX_UPLOAD_MB` | — | `5120` | Tope de subida (5 GB, el de AssemblyAI) |
| `BACKEND_URL` | — | `http://localhost:8000` | URL que consume el frontend |

> ⚠️ El fichero `.env` está en `.gitignore`. **Nunca subas tus claves al repositorio.**

---

## Uso

Arranca el backend en una terminal:

```bash
uvicorn backend.main:app --reload --port 8000
```

Y el frontend en otra:

```bash
streamlit run frontend/app.py
```

Abre <http://localhost:8501>, sube la grabación y pulsa **Transcribir y generar
apuntes**. El procesado corre en segundo plano: puedes cerrar el navegador y
volver más tarde. Una clase de 4 h suele tardar entre **10 y 30 minutos**.

La documentación interactiva de la API queda en <http://localhost:8000/docs>.

### Desde la línea de comandos

```bash
# Encolar una clase
curl -F "file=@clase_calculo.mp3" http://localhost:8000/api/jobs

# Consultar el estado
curl http://localhost:8000/api/jobs/{job_id}

# Descargar los apuntes cuando el estado sea "completed"
curl http://localhost:8000/api/jobs/{job_id}/notes -o apuntes.md
```

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado del servicio y configuración activa |
| `POST` | `/api/jobs` | Sube una grabación y encola el trabajo |
| `GET` | `/api/jobs` | Lista los trabajos recientes |
| `GET` | `/api/jobs/{id}` | Estado completo, transcripción y apuntes |
| `GET` | `/api/jobs/{id}/notes` | Apuntes en Markdown |
| `GET` | `/api/jobs/{id}/transcript` | Transcripción con oradores y tiempos |
| `DELETE` | `/api/jobs/{id}` | Borra el trabajo y sus ficheros |

---

## Formato de los apuntes generados

```markdown
# Introducción a las derivadas parciales

> **Duración:** 3 h 12 min · **Oradores:** Orador A, Orador B · **Fecha de procesado:** 21/08/2026

## Resumen ejecutivo
De qué trató la clase, qué tesis se defendió y qué debería llevarse el estudiante.

## Temas clave
- **Regla de la cadena** — por qué importa dentro de la clase. `[00:42:15]`

## Notas detalladas
### `[00:42:15]` Regla de la cadena
- Definición textual en *cursiva*, fórmulas en bloques de código.
- **Ejemplo:** desarrollo resuelto paso a paso.
- **⚠️ Ojo:** advertencias del docente ("esto entra en el examen").
- **P:** pregunta del alumnado · **R:** respuesta.

## Tareas y lecturas pendientes
- [ ] Ejercicios 4.1 a 4.12 — **entrega el viernes**

## Glosario
Terminología nueva, con la definición tal y como se explicó.
```

Las marcas de tiempo `[HH:MM:SS]` provienen de la diarización y permiten volver
al momento exacto del audio original.

---

## Detalles de implementación

**Subidas pesadas.** El fichero se escribe a disco en bloques de 4 MB y se envía
al proveedor en bloques de 5 MB. Una grabación de 2 GB nunca se carga entera en
memoria. Si se supera `MAX_UPLOAD_MB`, la subida se aborta y el fichero parcial
se borra en lugar de llenar el disco.

**Trabajos en segundo plano.** Ninguna conexión HTTP aguanta las horas que puede
durar el proceso, así que `POST /api/jobs` devuelve el id de inmediato y el
cliente consulta el estado por separado. El estado vive en SQLite, de modo que un
reinicio del servidor no pierde el trabajo.

**Integración con Claude.** Se usa `messages.stream()` — con `max_tokens` alto,
una petición sin streaming chocaría contra el timeout HTTP del SDK. Va con
*adaptive thinking* (el modelo decide cuánto razonar en cada sección) y
`output_config.effort` para regular el gasto. Tanto el prompt de sistema como la
transcripción llevan `cache_control`, así que regenerar los apuntes de una misma
clase cuesta una fracción de la primera pasada. Se comprueba
`stop_reason == "refusal"` antes de leer la respuesta, porque Claude Opus 5 puede
declinar una petición devolviendo un HTTP 200.

**Map-reduce de respaldo.** Por encima de `annotation_single_pass_char_limit`
(~1,2 M de caracteres, muy por encima de las 4 h objetivo) el anotador trocea la
transcripción **por líneas completas** — nunca a mitad de una intervención —,
procesa los bloques en paralelo y fusiona los extractos en una pasada final. En
el caso de uso normal esta ruta no se activa nunca.

---

## Tests

```bash
pytest
```

Los 24 tests cubren el formateo de la transcripción, la normalización de las
respuestas de AssemblyAI y Deepgram, la persistencia y el flujo completo de la
API. Usan proveedores simulados: **no gastan ni una llamada a las APIs de pago**.

---

## Licencia

MIT
