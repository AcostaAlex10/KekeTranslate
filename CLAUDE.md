# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Idioma

Todo el proyecto está en español, y hay una convención con dos mitades:

- **Lo que ve el usuario lleva tildes y eñes.** `tests/test_textos.py` lo
  comprueba con un análisis AST de las cadenas que llegan a `st.error(...)`,
  `HTTPException(detail=...)`, `AnnotationError(...)` y similares. Escribir
  «Anadir» rompe el test.
- **Los comentarios y docstrings van sin acentos.** El guardián los deja fuera a
  propósito. No los «arregles»: mantener las dos mitades como están evita
  diferencias de codificación en Windows.

Cuidado al pasar identificadores internos: un slug como `"transcripcion"`
dispara el guardián aunque nunca se muestre. Elige otro (`"texto"`) en vez de
debilitar el test.

## Comandos

El intérprete vive en `.venv`. En Windows, los comandos con salida acentuada
necesitan `PYTHONIOENCODING=utf-8` o revientan con `UnicodeEncodeError`.

```bash
# Arrancar backend + frontend, comprobar claves y abrir el navegador
./.venv/Scripts/python.exe run.py

# Pedir las claves por teclado y escribirlas en el .env (no se ven en pantalla)
./.venv/Scripts/python.exe run.py --configurar --visible

# Por separado
uvicorn backend.main:app --reload --port 8000
streamlit run frontend/app.py

# Tests
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest -q
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest tests/test_auth.py -q
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest \
    tests/test_auth.py::test_una_persona_no_ve_las_clases_de_otra -q
```

Los tests no llaman a ninguna API externa. Tardan alrededor de un minuto porque
los de interfaz arrancan Streamlit con `AppTest`.

## Arquitectura

El requisito que condiciona todo es la **duración**: una clase de 2–4 h son
cientos de MB de audio y 50–70k tokens de transcripción.

```
Streamlit ──subida en bloques──► FastAPI ──tarea en segundo plano──► pipeline
    ▲                              │                                    │
    └──── sondeo GET /api/jobs ────┴──── SQLite ◄────────────────────────┘
```

**La transcripción se persiste antes de pasar por el LLM.** Es la parte cara
—se paga por minuto de audio— así que si la anotación falla no se pierde:
`POST /api/jobs/{id}/reanotar` rehace solo los apuntes. Cualquier cambio en el
pipeline debe preservar esa propiedad.

**Y la transcripción no se traduce nunca.** `Job.idioma_apuntes` cambia el
idioma de los apuntes y solo de ellos: la transcripción es el registro de lo que
se dijo. `tests/test_idioma_de_los_apuntes.py` lo fija.

**Proveedores intercambiables por `.env`.** `transcription/factory.py` y
`annotator/factory.py` eligen implementación; la lógica común
—troceado, metadatos, map-reduce— vive en las clases `base.py`, y cada proveedor
solo implementa la llamada.

**Tres almacenes sobre un único fichero SQLite** (`storage/keketranslate.db`):

| Clase | Tablas | Detalle |
|---|---|---|
| `store.JobStore` | `jobs` | El `Job` se guarda como **JSON en una columna `payload`** |
| `biblioteca.Biblioteca` | `grupos`, `temas`, `materiales`, `notas` | Columnas normales |
| `usuarios.Usuarios` | `usuarios`, `sesiones`, `estados_oauth` | Argon2id, testigos opacos |

Trampa conocida: añadir un campo a `Job` **no** basta. `JobStore.list()`
construye el `JobSummary` a mano campo por campo, así que hay que añadirlo ahí
también o el listado lo devolverá vacío en silencio.

### Autenticación: negación por defecto

Un middleware en `backend/main.py` cierra **todo** `/api` salvo `RUTAS_ABIERTAS`
(`/api/health`, `/api/auth/`, `/api/compartido/`). No se protege endpoint por
endpoint a propósito: con treinta endpoints, olvidarse de uno no se notaría —el
endpoint seguiría funcionando, para cualquiera—. Así, lo que se olvida deja de
responder.

Al añadir un endpoint nuevo bajo `/api`, queda cerrado automáticamente; usa
`Depends(usuario_actual)` para obtener la identidad y los ayudantes
`_require_grupo` / `_require_job` / `_require_tema` / `_require_nota`, que
comprueban propiedad.

**Lo ajeno devuelve 404, nunca 403.** Un 403 confirmaría que ese identificador
existe. `tests/test_auth.py` lo verifica.

**El enlace compartido es la única puerta sin cuenta.** Vive en
`/api/compartido/{token}/...` y solo abre el grupo al que apunta. No reutilices
los endpoints normales para el visitante: exigen sesión.

## Streamlit: restricciones que ya causaron fallos

Están documentadas en comentarios largos dentro de `frontend/app.py`. Las que
más cuestan de redescubrir:

- **El cuerpo de un `st.expander` se ejecuta esté abierto o cerrado.** Poner una
  petición dentro de un desplegable por fila cuesta N peticiones por pantalla.
  `tests/test_coste_de_pantalla.py` cuenta peticiones para impedir la recaída.
- **Los expanders no se anidan**: `StreamlitAPIException` en tiempo de
  ejecución, no al importar. `tests/test_composicion.py` sigue el grafo de
  llamadas para cazarlo, porque los dos contenedores suelen estar en funciones
  distintas.
- **`st.tabs` vuelve a la primera pestaña en cada recarga** y ejecuta el
  contenido de todas. Por eso la navegación es un `st.radio` con clave.
- **Las opciones de los selectores son identidades, no etiquetas**
  (`"clases"`, `"subir"`, `"apuntes"`), con `format_func` poniendo el texto. Si
  la opción fuera el texto visible, cambiar una palabra invalidaría el estado
  guardado en sesión.
- **No se puede modificar `st.session_state[clave]` de un widget ya dibujado.**
  Para navegar desde un botón, usa `on_click=`, que corre antes del repintado.
- **`st.cache_data` y `st.cache_resource` son globales del servidor**, no por
  usuario. El testigo de sesión entra en la clave de caché de `_leer()`: sin eso,
  la segunda persona en pedir `/api/jobs` recibiría la lista de la primera.
- **`st.context.cookies` solo lee, y lee la petición inicial.** No hay forma de
  escribir cookies desde Python: se ponen con un componente de altura cero que
  toca `document.cookie` del documento padre. Y lo que devuelve no cambia en
  toda la sesión aunque el navegador sí haya cambiado, así que la cookie de
  sesión se mira **una sola vez** (`cookie_ya_mirada`); releyéndola en cada
  pasada, *Salir* no funciona. `tests/test_cookie_de_sesion.py` lo fija.
- **Tras un `st.rerun()`, `AppTest` conserva los elementos de la pasada
  abandonada.** Un test que compruebe «ya no está el botón X» pasará a verde
  por error. Comprueba la presencia de algo de la pantalla nueva.

### Iconos y CSS

Los iconos son **Material Symbols** (`:material/nombre:` en etiquetas markdown,
`icon=` en botones y alertas), nunca emojis. La única excepción es el
`page_icon` de la pestaña del navegador, donde Streamlit solo acepta emoji.

Hay tres bloques de CSS inyectado, y todos existen por la misma razón: el token
`primaryColor` tiene que ser **oscuro** para que el texto blanco del botón
primario se lea, pero Streamlit lo usa además como **color de letra** de la
opción activa, del hover y del foco, y sobre este lienzo casi negro eso da
3,41:1. Está calculado en los comentarios de `.streamlit/config.toml`: con este
fondo no existe ningún color que cumpla ambas cosas.

Los enganches son `data-testid` y `st-key-<clave>` —esta última la deriva
Streamlit de la clave que le pones tú al widget—, no clases generadas. Si alguna
desapareciera en una versión futura, lo peor que ocurre es volver al aspecto
anterior. Al añadir CSS, mantén ese criterio.

## Versiones fijadas por un motivo

- **Streamlit 1.42.2.** Es la primera con `st.context.cookies`. No subir más: de
  1.43 en adelante exige `starlette >= 0.46` y FastAPI 0.115 exige `< 0.42`
  —probado, el backend deja de importar—. Subir obliga a subir FastAPI también.
- **`GEMINI_MODEL=gemini-3.7-flash`**, versión fija y no el alias
  `gemini-flash-latest`: el alias apunta al modelo más nuevo, y esos llegan con
  un nivel gratuito de **20 peticiones al día**. El riesgo de que se retire está
  cubierto: ante un 404 el mensaje de error dice qué modelo poner.

## Dónde están las decisiones

Antes de cambiar algo con implicaciones de producto, lee:

- **`PRODUCT.md`** — quién lo usa, qué está decidido y qué no. La única
  restricción marcada como innegociable son los apuntes privados por defecto.
- **`docs/ESTADO.md`** — lo que solo se aprende probando contra las APIs reales,
  incluidas dos explicaciones que resultaron falsas y por qué lo parecían.
- **`docs/PENDIENTE.md`** — lo que queda, en orden, con lo que hay que decidir
  antes de empezar cada cosa.

Los mensajes de commit de este repositorio explican el **porqué**, no solo el
qué; sigue esa costumbre.
