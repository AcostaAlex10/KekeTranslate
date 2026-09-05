# Estado del proyecto

Documento de situación a **31/08/2026**. Para instalación, variables de entorno,
endpoints y formato de los apuntes, ver el [README](../README.md); aquí está lo
que el README no cuenta: en qué punto está la app, qué trampas tiene, y qué
falta.

---

## 1. Qué funciona hoy

La app hace el recorrido completo: **grabación → transcripción → apuntes en
Markdown**. Dos formas de meter el audio:

| Vía | Estado | Notas |
|---|---|---|
| Subir un fichero ya grabado | ✅ Estable | Audio y vídeo (del vídeo solo se aprovecha el sonido) |
| Grabar desde la propia app | ⚠️ Provisional | Sirve para clases cortas; ver *Limitaciones* |

**71 tests en verde**, ninguno gasta llamadas a las APIs de pago.

### Lo que está probado de verdad

Contra la app corriendo, no solo en tests: `GET /api/health`, rechazo de
formatos no soportados (415), fichero vacío (400), fichero por encima del tope
(413) borrando el parcial, trabajo inexistente (404), resultados no disponibles
(409), borrado (204) limpiando el audio del disco, y el ciclo completo de un
trabajo que falla y guarda el motivo.

### Probado de punta a punta con servicios reales (31/08/2026)

Se procesó una clase de prueba de 91 segundos con **AssemblyAI + Gemini**:
subida → transcripción con oradores → apuntes. **40 segundos** en total, de la
subida al Markdown final.

La transcripción salió con puntuación, acentos y los números hablados bien
convertidos (`4.1 al 4.12`, `unidad 3`). Los apuntes recogieron el ejemplo
resuelto con las fórmulas en LaTeX, la advertencia del docente sobre el
parcial, la pregunta del alumnado como P/R, las dos tareas con su fecha de
entrega y el glosario.

### Prueba con una clase larga (31/08/2026)

Se generó una clase de **4 min 33 s** con once secciones separadas por pausas
de 2 segundos, para ver cómo se comporta la app con material más parecido al
real. Destapó tres fallos, todos ya corregidos, y confirmó un límite del
nivel gratuito de Gemini.

**Las marcas de tiempo se colapsaban en `[00:00:00]`.** AssemblyAI corta sus
`utterances` al cambiar de orador, no en las pausas. Una clase con un solo
profesor volvía como **una única intervención**: una sola línea con una sola
marca. Como los apuntes citan el momento de cada tema, la función quedaba
inservible justo en el caso de uso principal.

Arrastraba un segundo fallo más grave y menos visible: el troceador del
anotador corta por saltos de línea, así que con una sola línea **no podía
cortar nada**. El map-reduce que existe para las clases larguísimas devolvía
un bloque único del tamaño de la clase entera, es decir, no hacía nada
precisamente cuando hacía falta.

La corrección parte cada intervención larga cada ~40 s usando los tiempos por
palabra que AssemblyAI ya devuelve, cortando al final de una frase. Resultado
medido sobre la misma clase: **de 1 línea a 7**, y en los apuntes generados
las marcas pasaron de ser todas `[00:00:00]` a `[00:00:00]`, `[00:00:41]`,
`[00:02:09]`, `[00:02:54]`.

**Una transcripción que fallaba al anotar se perdía.** Tres ejecuciones
seguidas fallaron al generar los apuntes; en las tres, la transcripción (lo
que se paga y lo que más tarda) estaba hecha y guardada, pero no había forma
de aprovecharla: el mensaje decía "vuelve a intentarlo más tarde" y no existía
ningún reintento. Ahora hay un botón **Reintentar apuntes** que reanuda solo
esa parte. Verificado sobre un trabajo real varado: completado en 60 s sin
volver a subir ni pagar el audio.

**Variables duplicadas en el `.env`.** El fichero tenía a la vez
`GEMINI_API_KEY` y `Gemini_API_KEY`, con el mismo valor. Lo dejó la propia
función de reparación: comparaba los nombres respetando mayúsculas, así que no
reconoció la línea existente y añadió otra. Pydantic no distingue mayúsculas y
la app arrancaba igual, de modo que el síntoma habría aparecido mucho después,
al editar la línea equivocada y ver que no pasaba nada.

### El nivel gratuito de Gemini no da respuestas largas

Comprobado por bisección, no por suposición. Con el mismo modelo y la misma
clave, el mismo día:

| Petición | Resultado |
|---|---|
| Entrada corta, salida corta | OK |
| Clase entera, resumen en 5 puntos | OK |
| Clase entera, `max_output_tokens` 32 000 | OK |
| Petición de apuntes completos (salida larga) | **503** |
| "Escribe 500 palabras" | **503** |

**Corregido el 04/09/2026.** Aquella conclusión —"es la longitud de la
generación"— era falsa. Con una clave y un proyecto recién creados, o sea con
la cuota del día intacta, se midió esto:

| Prueba | Resultado |
|---|---|
| Tras 90 s sin tocar la API, primera llamada | OK, 4.052 caracteres |
| Segunda llamada seguida | OK, 3.982 caracteres |
| Tercera llamada seguida | **503** |
| Misma llamada, sin ningún parámetro | **503** |

O sea: la misma petición idéntica responde o falla según **cuántas se hayan
hecho en el último minuto**. No es la longitud, ni los parámetros, ni la cuota
diaria. Es un **límite de peticiones por minuto** que Google notifica como un
503 "high demand" en vez de como un 429.

Y explica por qué falla justo la anotación: `backend/annotator/base.py` lanza
los fragmentos **en paralelo** con `asyncio.gather`. Con un límite de dos o tres
por minuto, disparar N a la vez garantiza que casi todas fallen; y los
reintentos, que también salen a la vez, vuelven a chocar contra el mismo techo.

El arreglo no es esperar más: es **no lanzarlas todas juntas**. Está anotado en
`PENDIENTE.md`.

Importa porque el mensaje de error decía "está saturado, vuelve a intentarlo",
que manda a reintentar en bucle sin éxito. Ahora el mensaje nombra la cuota y
apunta al botón de reintento. Más tarde el mismo día la cuota se recuperó y la
generación completa funcionó.

### El nivel gratuito de Gemini no es la prueba de 90 días (04/09/2026)

Se confundieron dos cosas y costó tiempo, así que queda escrito:

| | Nivel gratuito de la API de Gemini | Prueba de Google Cloud |
|---|---|---|
| Qué es | Acceso permanente con límites de uso | 300 USD de crédito |
| Caduca | **No** | Sí, a los 90 días |
| Pide tarjeta | No | Sí |
| Sirve para Gemini | Sí | **No**, desde marzo de 2026 |

Es decir: el crédito de la prueba **ni siquiera se puede gastar** en la API de
Gemini, así que nada de lo que usa esta app depende de él ni se muere con él.

Los límites reales del nivel gratuito, que sí importan: unas **250 peticiones
al día** por clave, y **solo modelos Flash** —los Pro pasaron a exigir
facturación en mayo de 2026—. Esta app usa `gemini-flash-latest`, así que cae
justo dentro.

**La consecuencia práctica:** al crear el proyecto en la consola de Google
Cloud, **no le actives la facturación**. Sin cuenta de facturación asociada, el
proyecto se queda en el nivel gratuito y no hay forma de que aparezca un cobro.

### Las claves `AQ.` de AI Studio no sirven para esta API (04/09/2026)

Al rotar las claves, la nueva de Gemini empezaba por `AQ.` y medía 53
caracteres. La API la rechaza así:

```
401 UNAUTHENTICATED — ACCESS_TOKEN_TYPE_UNSUPPORTED
Request had invalid authentication credentials. Expected OAuth 2 access token,
login cookie or other valid authentication credential.
```

No es un error de configuración de esta app ni un pegado a medias: **es un
problema conocido y abierto de Google**. Hay cuentas a las que AI Studio solo
les emite claves con prefijo `AQ.`, y `generativelanguage.googleapis.com` solo
acepta las del formato antiguo, `AIza…` de unos 39 caracteres. Hay hilos en el
foro oficial desde junio de 2026 pidiendo que lo arreglen.

**Qué hacer si aparece.** Por orden de menos a más trabajo:

1. Crear la clave desde la **consola de Google Cloud** en vez de desde AI
   Studio: activar la *Generative Language API* en un proyecto y crear ahí una
   clave de API. Esa vía suele seguir dando el formato `AIza…`.
2. Probar con otra cuenta de Google, porque la restricción es por cuenta.
3. Cambiar de anotador a Claude, que la app ya soporta:
   `python run.py --configurar --avanzado`. Es de pago, pero no tiene el
   problema.

**Cómo reconocerlo rápido:** una clave de Gemini válida empieza por `AIza` y
mide unos 39 caracteres. Si empieza por `AQ.`, no va a funcionar por mucho que
se pegue bien.

### Lo que NO está probado

- El anotador de **Claude** con llamadas reales (sí el de Gemini).
- Una clase de **4 horas**: lo más largo probado son 4 min 33 s.
- Grabar desde el **móvil**.
- Que el **PDF del programa cambie de verdad los apuntes** en una ejecución
  real. Está probado que el texto del PDF llega al prompt (hay tests que lo
  comprueban sobre el prompt montado), pero la generación final quedó bloqueada
  por la cuota agotada de Gemini. Es lo primero que hay que mirar mañana.
- Subir un **vídeo** (la app lo acepta y AssemblyAI extrae el audio, pero no se
  ha ejecutado ni una vez).

---

## 2. Cómo probarla

### Requisitos

Python 3.12 (o 3.11). El entorno ya está creado en `.venv/`.

### Opción gratuita

Anthropic no tiene nivel gratuito. Para probar sin pagar, el anotador puede
usar **Google Gemini**, que sí lo tiene, con la misma ventana de 1M de
contexto. En el `.env`:

```
ANNOTATOR_PROVIDER=gemini
GEMINI_API_KEY=tu_clave
```

La clave se saca gratis en https://aistudio.google.com/apikey con una cuenta de
Google. Para la transcripción, **Deepgram** también tiene créditos gratis de
inicio y ya está implementado: `TRANSCRIPTION_PROVIDER=deepgram`.

### Paso 1: las claves

Sin esto la app arranca y acepta grabaciones, pero **todos los trabajos
terminan en `failed`** con el mensaje *"Falta ASSEMBLYAI_API_KEY"*.

Lo más cómodo es que el lanzador las pida y las escriba:

```bash
python run.py --configurar
```

Pregunta el proveedor de transcripción y el de apuntes, y luego cada clave. Lo
que se teclea no se ve en pantalla ni queda en el historial del terminal, y el
resto del `.env` (comentarios incluidos) se conserva intacto.

### Paso 2: arrancar

Un solo comando levanta los dos servicios, avisa si faltan claves, espera a que
el backend responda y abre el navegador:

```bash
cd "E:\claude code\keketranslate"; .\.venv\Scripts\python.exe run.py
```

O **doble clic en `iniciar.bat`**. Para el movil: `run.py --red`.

### Paso 3: grabar

En *Nueva clase* → **🎙️ Grabar ahora**, botón *Record*. El micrófono funciona
sin configurar nada porque `localhost` cuenta como sitio seguro para el
navegador. Grabar 30 segundos hablando, parar, y pulsar *Transcribir y generar
apuntes*. El avance se sigue en la pestaña *Mis clases*.

Para probar desde el **móvil**, ver [movil.md](movil.md): hace falta HTTPS y
tiene bastante más fricción.

### Comprobar que nada se rompió

```bash
cd "E:\claude code\keketranslate"; .\.venv\Scripts\python.exe -m pytest -q
```

---

## 3. Cosas que hay que saber

**El micrófono exige contexto seguro.** Los navegadores solo dan acceso al
micrófono por `https://` o `localhost`. Si se entra por
`http://192.168.1.34:8501`, Chrome deniega el micrófono **sin llegar a
preguntar**. No es configurable. Por eso el acceso desde el móvil necesita
certificados y por eso probar en la propia PC es tan sencillo en comparación.

**Norton intercepta el TLS, y eso rompía todas las llamadas a las APIs.**
Norton Web/Mail Shield sustituye el certificado de cada sitio por uno propio,
firmado por `Norton Web/Mail Shield Root`. Se comprobó que lo hace también con
`api.assemblyai.com`, `api.anthropic.com` y `generativelanguage.googleapis.com`.

El navegador lo acepta porque consulta el almacén de Windows, donde Norton
instaló su CA; Python no, porque usa la lista fija de `certifi`. El resultado
era `CERTIFICATE_VERIFY_FAILED` en **todas** las llamadas, aun con las claves
correctas — un síntoma que despista muchísimo, porque el navegador entra sin
problema a las mismas páginas.

Resuelto en `backend/tls.py`: al arrancar se redirige la verificación al
almacén del sistema con `truststore`. No se desactiva nada, se valida contra la
lista que el sistema considera de confianza. Si usas otro antivirus con
"escaneo SSL" (Kaspersky, ESET, Avast), el problema y la solución son los
mismos.

Para los certificados **locales** del modo móvil sigue aplicando lo mismo: si
en el móvil aparece una advertencia y el emisor es Norton, hay que excluir el
puerto en su configuración. La cadena propia es correcta:
`openssl verify -CAfile ca-cert.pem server-cert.pem`.

**Hay dos límites de subida, y manda el más bajo.** El backend acepta 5 GB
(`MAX_UPLOAD_MB`), pero Streamlit corta antes, en 1 GB
(`.streamlit/config.toml`), porque bufferea la subida en memoria: ese número es
también el pico de RAM. La barra lateral muestra el mínimo de los dos, para que
la pantalla no pueda contradecirse.

**No hay usuarios ni login.** Quien abra la página ve **todos** los trabajos,
los suyos y los de cualquier otro. Es aceptable en local; deja de serlo en el
momento en que la app se publique en internet.

**El audio se conserva si el trabajo falla**, para poder reintentar, y se borra
al completarse. La transcripción se vuelca a disco *antes* de pasar por Claude,
así que un fallo del anotador no tira a la basura la parte cara.

**Las claves nunca al repositorio.** `.env`, `certs/` y `*.pem` están en
`.gitignore`.

---

## 4. Limitaciones de la grabadora actual

La grabadora usa `st.audio_input` de Streamlit y es **provisional a
propósito**. Dos problemas para una clase larga:

1. **Guarda el audio sin comprimir.** Unos 345 MB por hora (estimación: WAV a
   48 kHz, 16 bits, mono). Una clase de 4 h rondaría 1,4 GB — por encima del
   tope de 1 GB.
2. **No envía nada hasta que paras.** Todo vive en la memoria del móvil
   mientras grabas, y si la pestaña se cierra o el navegador la suspende (algo
   habitual al apagar la pantalla), se pierde la clase entera.

Sirve para validar el circuito con grabaciones cortas. Para clases de verdad,
hoy la vía fiable es grabar con la app del móvil y subir el fichero.

La grabadora definitiva necesita una página propia servida por FastAPI que
comprima y **suba en trozos mientras graba**, de modo que una caída no cueste
más que el último fragmento.

---

## 5. Qué se hizo (y por qué)

### Corrección de un fallo que impedía arrancar la API

`backend/main.py` tiene `from __future__ import annotations`, lo que convierte
las anotaciones en cadenas. FastAPI deducía el `response_model` del tipo de
retorno y, al evaluar `"None"`, obtenía la clase `NoneType` en lugar del valor
`None`. Como `NoneType` es *truthy*, saltaba la validación de FastAPI:
`Status code 204 must not have a response body`. El módulo no se podía
importar, así que **la API entera no arrancaba**. Se resolvió declarando
`response_model=None` en el `DELETE`.

### La pantalla se contradecía sobre el tamaño máximo

Decía *"Subida máxima: 5.0 GB"* junto a un selector que rechazaba a los 200 MB
(el valor por defecto de Streamlit). Se fijó el tope de Streamlit en 1 GB y,
más importante, la barra lateral ahora calcula el **mínimo entre los dos
límites**: así el error no puede reaparecer si mañana cambia cualquiera de
ellos.

### El backend no arrancaba con el `.env` de ejemplo

`.env.example` trae `EXPECTED_SPEAKERS=` sin valor, y pydantic intentaba
convertir esa cadena vacía en un entero:
`Input should be a valid integer [input_value='']`. El resultado es que
**cualquiera que siguiera el README no podía arrancar la app**. Ahora una
variable numérica vacía se trata como "no configurada" y se usa el valor por
defecto. En los campos de texto el vacío se respeta, porque ahí sí significa
algo: `TRANSCRIPTION_LANGUAGE` vacío pide detección automática del idioma.

### Arrancar era incómodo

Hacían falta dos terminales con comandos largos. `run.py` levanta ambos
servicios, comprueba dependencias y puertos, crea el `.env` la primera vez,
avisa de las claves que falten y abre el navegador. `iniciar.bat` hace lo mismo
con doble clic.

### Alternativa gratuita al anotador

Anthropic no tiene nivel gratuito, así que probar la app costaba dinero sí o
sí. Se añadió un anotador de **Gemini**, que sí lo tiene. Para no duplicar
código, la lógica común (troceado por líneas, metadatos, map-reduce, limpieza
de la salida) se extrajo a `annotator/base.py`; cada proveedor solo implementa
la llamada al modelo. Gemini necesita además detectar dos finales anómalos que
Claude no tiene: respuesta cortada por límite de tokens y bloqueo del filtro de
contenidos, que devuelven un 200 con unos apuntes truncados o vacíos.

### Todas las llamadas a las APIs fallaban por el antivirus

Ver *Cosas que hay que saber*. Es el hallazgo más importante de esta sesión:
sin resolverlo, cargar las claves no habría servido de nada.

### Dos fallos que solo aparecieron al llamar a las APIs de verdad

Ninguno de los dos podía verse con proveedores simulados, y los dos rompían el
flujo por completo:

1. **AssemblyAI retiró el parámetro `speech_model`.** La API responde 400 y
   pide `speech_models` (en plural, una lista por orden de preferencia). El
   código estaba escrito contra la versión anterior.
2. **Google retiró `gemini-2.5-flash` para cuentas nuevas.** Devuelve 404 con
   un mensaje que indica el sustituto (`gemini-3.6-flash`). Por eso el error de
   Gemini ahora conserva siempre el texto original de Google: es donde viene el
   dato que resuelve el problema.

De paso se añadieron **reintentos ante 503** en el anotador. Llegados a ese
punto la transcripción ya está hecha y pagada; abandonar por una saturación
pasajera de Google sería tirar la parte cara del trabajo.

### El asistente de configuración inducía a error

Preguntaba primero por el proveedor y después por la clave, así que era fácil
pegar la clave en el campo del proveedor. El resultado era peor que el error:
un proveedor inválido **impide arrancar el backend** con un mensaje de
validación que no explica nada. Ahora los proveedores se eligen por número (no
admite texto libre), sólo se preguntan con `--avanzado`, y `reparar_env()`
detecta y corrige un `.env` que ya haya caído en la trampa, moviendo la clave a
su campo sin llegar a mostrarla.

### Un mensaje de error que engañaba

Un trabajo fallido respondía *"Los apuntes aún no están listos (estado:
failed)"*. No es que no estuvieran listos: no iban a estarlo nunca. Ahora
devuelve el motivo real del fallo.

### Grabar desde la app

Modo *Grabar ahora* en la pestaña de subida, y certificados para servir por
HTTPS a la red local. De paso: lo grabado no trae nombre de fichero y el
backend valida el formato **por la extensión**, así que sin bautizarlo cada
grabación habría sido rechazada con un 415.

### Grupos por materia

Un grupo por asignatura, con temas dentro, PDFs que la IA lee, notas propias y
un enlace para compartirlo. Decisiones que conviene conocer:

- **El material se lee, no solo se guarda.** El texto del PDF entra en el prompt
  antes de la transcripcion, con instrucciones explicitas: sirve para situar la
  clase en el programa y usar la terminologia de la catedra, pero **no** es
  fuente. El modelo tiene prohibido anadir contenido del programa que el docente
  no llego a explicar, y si la clase contradice al material, manda la clase.
- **Un PDF escaneado se rechaza.** Sin OCR no hay texto que extraer. Guardarlo
  en silencio haria creer que la IA lo esta teniendo en cuenta, asi que se avisa.
- **El material tiene presupuesto propio** (`ANNOTATION_MATERIAL_CHAR_LIMIT`),
  repartido entre los documentos. Un practico enorme no puede desplazar a la
  transcripcion, que es lo que hay que anotar.
- **Borrar no encadena hacia las clases.** Borrar un grupo elimina sus temas,
  material y notas, pero las clases transcritas quedan sin archivar. Una clase
  cuesta dinero y una espera larga: que desaparezca por reorganizar carpetas
  seria desproporcionado. Borrar un tema tampoco borra su material ni sus notas.
- **Los apuntes editados a mano se guardan aparte** de los que genero la IA, de
  modo que rehacerlos no se lleva por delante las correcciones propias.
- **El enlace compartido admite lectura o escritura**, a eleccion del autor.
  Cambiar el permiso no invalida el enlace; revocarlo si.

### Un hueco conocido: el enlace protege el enlace, no la API

Mientras no haya usuarios, **la API no autentica nada**. El token hace que un
enlace lleve a un grupo concreto, pero quien pueda alcanzar el backend por su
cuenta puede consultar cualquier grupo. Por eso la app se sirve solo en la red
local. El login es lo que cierra este hueco y sigue siendo el siguiente paso.

### Tests: de 24 a 131

El hueco grande era `pipeline.py` — **cero tests**, siendo el fichero que
promete "nunca propaga excepciones".

| Fichero | Cubre |
|---|---|
| `test_pipeline.py` (8) | Fallos de transcripción y de anotación, errores imprevistos, el audio que sobrevive al fallo, la transcripción que no se pierde, proveedor sin diarización, progreso |
| `test_api.py` (+5) | 413 por tamaño, 409 en curso, 409 de trabajo fallido, borrado inexistente, error en el listado |
| `test_frontend.py` (4) | Que la página cargue aunque el backend esté caído, y las dos vías de carga |
| `test_config.py` (5) | Que una variable vacía en el `.env` no impida arrancar |
| `test_annotator_gemini.py` (19) | Elección de anotador, respuestas anómalas de Gemini y qué fallos merecen reintento |
| `test_run.py` (6) | Que escribir en el `.env` no destruya la configuración del usuario |

Los tests se verificaron rompiendo el código a propósito para comprobar que
fallaban: un test que nunca ha fallado no demuestra nada.

---

## 6. Pendiente

### Decidido y por hacer, en orden

- [ ] **Grabadora seria**: página propia con compresión y subida por trozos.
      Es lo que hace viable una clase de 4 h desde el móvil.
- [ ] **Usuarios y login.** Desbloquea dos cosas a la vez: los apuntes privados
      y la posibilidad de publicar la app en internet.
- [ ] **Sesión compartida**: varias personas grabando la misma clase en
      simultáneo y la IA cruzando las pistas. La parte difícil es sincronizar
      los relojes de los dispositivos; el coste se multiplica por el número de
      pistas.
- [ ] **Despliegue real** con certificado de verdad, para que nadie tenga que
      instalar nada. Requiere el login antes.

### Menor

- [ ] El firewall de Windows no tiene abierto el puerto 8501; sin eso el móvil
      no llega. Requiere permisos de administrador.
- [ ] `JobStore` abre una conexión SQLite por operación y no la cierra
      explícitamente (depende del recolector de basura). No ha dado problemas,
      pero en Windows puede dejar el fichero bloqueado.

---

## 7. Decisiones tomadas

| Decisión | Motivo |
|---|---|
| La app **graba por sí misma** | Dejar el móvil cerca de quien habla, sin grabar aparte y volcar después |
| **Nunca graba vídeo** | Solo interesa el sonido; hoy la imagen se descarta con `-vn` |
| "Varios ángulos" es **audio**, no cámaras | Lo que se gana es cercanía a quien habla, no perspectiva visual |
| Apuntes **privados por defecto** | Compartir es una decisión explícita de cada persona |
| **Notion descartado** | Los apuntes viven en KekeTranslate. La API de Notion no acepta Markdown y habría que convertir a su formato de bloques, mucho trabajo para ahorrar un copiar-pegar |
| Acceso por **red local + certificado**, no túnel público | Nada sale a internet mientras no haya login |
| Un **grupo** es personal por defecto | Se comparte solo si su dueño lo habilita, por enlace o por correo |
| La IA **lee los PDFs** del grupo | Un adjunto que solo se descarga aporta poco; leído, permite situar la clase en el programa de la materia |
| `GEMINI_MODEL` usa el alias **`-latest`** | Fijar una versión concreta se retira sin aviso (la serie 2.5 ya da 404) y concentra la carga |
| Streamlit se sirve **sin HTTPS por defecto** | En `localhost` el micrófono ya funciona; el modo con certificados es un comando aparte |
