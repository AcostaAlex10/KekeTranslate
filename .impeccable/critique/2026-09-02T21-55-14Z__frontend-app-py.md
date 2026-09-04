---
target: frontend/app.py
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
timestamp: 2026-09-02T21-55-14Z
slug: frontend-app-py
---
Method: dual-agent (A: revisión de diseño · B: detector + evidencia de navegador)

## Design Health Score

| # | Heurística | Pts | Problema clave |
|---|---|---|---|
| 1 | Visibilidad del estado | 2 | Sin auto-refresco; `st.progress` con constantes fijas (0.05/0.2/0.6/0.9) que no se mueven en 20 min; sin ETA ni tiempo transcurrido |
| 2 | Correspondencia con el mundo real | 3 | La lista de material muestra el enum crudo (`programa`) mientras el formulario dice "Programa de la materia"; el placeholder de *Nombre* de grupo describe un tema |
| 3 | Control y libertad | 1 | Cuatro acciones destructivas sin confirmación ni deshacer; "Rehacer con la IA" pisa los apuntes sin preguntar; las advertencias van debajo del botón |
| 4 | Consistencia y estándares | 2 | "Rehacer con la IA" no hace `st.rerun()` y "Reintentar apuntes" sí; Grupos→Clases lista sin icono de estado; cadenas de Streamlit en inglés dentro de una UI en español |
| 5 | Prevención de errores | 2 | "Adjuntar" sin PDF y "Añadir nota" sin título no hacen nada ni dicen nada; selectores en estado sucio que contradicen el texto contiguo |
| 6 | Reconocer antes que recordar | 2 | Cuatro de cinco filas se llaman `clase_larga.wav`; Grupos→Clases lista la clase pero no enlaza a ella |
| 7 | Flexibilidad y eficiencia | 2 | Sin búsqueda, filtro ni orden; sin botón de copiar el enlace; sin renombrar clases ni temas |
| 8 | Estética y minimalismo | 2 | `layout="wide"` sin `max-width`: 904 px de párrafo a 1440 y 1584 px a 1920 (~190 caracteres por línea) para el contenido que el producto existe para leer |
| 9 | Recuperación de errores | 2 | Un 404 se muestra como caída de red: dos cajas rojas contradictorias, con la URL interna del backend y un enlace a MDN |
| 10 | Ayuda y documentación | 3 | Buen microcopy, pero el consejo accionable apunta a una variable de entorno inalcanzable desde la interfaz |
| **Total** | | **21/40** | Tramo bajo de lo normal |

Máximo aplicable: 40. Ninguna heurística quedó fuera de alcance.

## Design Specificity Verdict

**Evaluación de diseño (sin anclar):** anclado en este producto, con un techo claro. La nomenclatura reproduce la organización real de una cursada, y el copy dice cosas que solo aplican aquí. Pero la especificidad se queda en el **texto**, no en la **estructura**: la disposición es la que sale de Streamlit por defecto. La prueba es que la promesa central de PRODUCT.md —"cuando necesite volver, la marca de tiempo lo lleve al minuto exacto"— **no existe en la interfaz**: los `[00:02:54]` son código monoespaciado inerte y no hay reproductor de audio en ninguna pantalla.

**Escaneo determinista:** `detect.mjs --json frontend` devolvió `[]` con código de salida 0; repetido sobre la raíz, idéntico. Verificado leyendo el código del detector: `SCANNABLE_EXTENSIONS` cubre `.html .css .scss .jsx .tsx .js .ts .vue .svelte .astro`, y `.py` no está. El único fichero de interfaz del proyecto es `frontend/app.py`. No es un fallo silencioso: no hay nada que escanear.

**Superposiciones visuales:** no se realizó inyección de `detect.js` en la página, así que **no hay ninguna superposición visible en el navegador**. La evidencia determinista viene de mediciones directas del DOM, no del detector.

## Overall Impression

El copy es lo mejor que tiene y sostiene la nota; la estructura es la que vino de fábrica. La mayor oportunidad es el estado en vivo: el escenario que PRODUCT.md declara —una clase de cuatro horas procesándose sin supervisión— es exactamente donde la interfaz da menos.

## What's Working

1. **El copy de error conserva el texto del proveedor y da una salida.** El dato que resuelve el problema está en la primera frase, en español, antes del ruido técnico.
2. **La distinción versión-IA / versión-corregida está expuesta en la UI**, no solo en la base de datos. Hace visible una garantía que normalmente se descubre perdiendo trabajo.
3. **"Reintentar apuntes" está donde ocurre el fallo**, con la razón al lado, no en un menú ni en un modal.

## Priority Issues

### [P1] El enlace compartido recibe al invitado con una pantalla vacía o con un error falso
Dos defectos verificados en el mismo camino. `app.py:257` evalúa `if not clases` sobre la lista sin filtrar mientras el bucle descarta lo que no está `completed`: un grupo con clases fallidas renderiza la solapa "Clases" en blanco, sin mensaje. Y `api_get` (`app.py:68-74`) captura `httpx.HTTPError`, que incluye `HTTPStatusError`, así que un 404 se anuncia como "No hay conexión con el servidor" junto a la URL interna del backend. Afecta a todas las lecturas.
**Por qué importa:** compartir es la única vía por la que el producto llega a alguien que no eres tú, y ese desconocido no tiene forma de diagnosticar.
**Arreglo:** filtrar antes de comprobar y dar un vacío que explique; capturar `HTTPStatusError` aparte y reservar el copy de red para `TransportError`.
**Comando:** `/impeccable harden`

### [P1] La clase se procesa media hora y la pantalla no se entera sola
Sin auto-refresco, barra de progreso con constantes fijas, sin tiempo transcurrido ni estimación. `st.session_state["trabajo_activo"]` se escribe en `encolar()` y nunca se lee. Y la transcripción de una clase fallida no es visible ni descargable: la solapa solo se renderiza en la rama `completed`.
**Por qué importa:** es el escenario declarado. La app promete "puedes cerrar el navegador" y al volver no ofrece nada salvo un botón manual; cuando falla, esconde el activo que el Principio 1 promete no tirar nunca.
**Arreglo:** refrescar solo mientras haya clases activas, mostrar minutos transcurridos, y ofrecer la transcripción en la rama fallida afirmando que está guardada.
**Comando:** `/impeccable harden`

### [P1] En móvil, la navegación entera vive detrás de un botón de 32 px sin nombre accesible
Medido en el DOM: el control que abre la barra lateral es un botón de 32×32 px con `aria-label=""` y su SVG `aria-hidden="true"`. Al abrirse ocupa 336 de 375 px y se superpone al contenido. Las filas de navegación miden 26 px de alto. Además `document.documentElement.lang` es `"en"` en los seis estados medidos, con todo el contenido en español.
**Por qué importa:** el móvil es el dispositivo del escenario "durante la clase". Un primerizo no tiene ninguna pista de que existan otras secciones, y un lector de pantalla anuncia "botón" sin nombre en el único control de navegación.
**Arreglo:** sacar el selector de sección al cuerpo principal y dejar la barra lateral para estado y configuración; fijar el idioma del documento.
**Comando:** `/impeccable adapt`

### [P1] Cuatro acciones irreversibles a un clic, con la advertencia debajo del botón
"Borrar el grupo", "Borrar tema", "Quitar este documento" y "Borrar" nota ejecutan al primer clic, sin confirmación ni deshacer, y con las consecuencias explicadas después del botón. "Rehacer con la IA" pisa los apuntes generados sin preguntar, con el mismo peso visual que "Descargar apuntes".
**Por qué importa:** el Principio 4 ("Reorganizar no destruye") se está aplicando solo a las clases: borrar el grupo sí se lleva temas, material y notas.
**Arreglo:** mover la advertencia encima y exigir confirmación en las cuatro.
**Comando:** `/impeccable harden`

### [P2] Dos defectos medidos en la superficie que el producto existe para leer y pulsar
El botón primario "Crear" es blanco sobre el ámbar del tema: **ratio 2.16:1**, por debajo del 4.5:1 que exige WCAG AA, idéntico en escritorio y móvil. Y los apuntes se renderizan a ~190 caracteres por línea a 1920 px por `layout="wide"` sin `max-width`. Además `config.toml` documenta que la prosa "se pasa a serif desde el CSS de la app": ese CSS no existe.
**Por qué importa:** el éxito se define como "poder estudiar de esos apuntes", y el color del botón primario es una decisión de tema, no una limitación del framework.
**Comando:** `/impeccable colorize` y `/impeccable layout`

## Persona Red Flags

**Primerizo confundido:** en móvil no ve el chevron de 32 px y concluye que la app solo sirve para grabar. El placeholder de *Nombre* de grupo describe un tema, así que crea grupos por clase y rompe el modelo de datos. Pulsa "Adjuntar" sin elegir PDF y no pasa nada. Lee "Limit 1GB per file" a 20 px de "Hasta 50 MB por documento".

**Usuario dependiente de accesibilidad:** el único control de navegación en móvil no tiene nombre accesible (WCAG 4.1.2). La solapa activa se distingue solo por color, el mismo del `hover`. El estado de una clase se comunica solo por emoji. `lang="en"` sobre contenido en español. El área de transcripción es un `textarea`, así que el buscador del navegador no encuentra texto dentro.

**Estudiante que deja el móvil grabando cuatro horas:** el modo por defecto es "Grabar ahora", y justo debajo un aviso amarillo desaconseja usarlo para clases largas —después de que ya pulsó Record. Al volver no puede distinguir "sigue trabajando" de "se colgó". Si falló por cuota, el consejo apunta a una variable del `.env`. Y el `[00:47:12]` del apunte no lleva a ningún sitio.

## Minor Observations

- Falta la tilde en un error ya persistido en la base de datos ("Se agot**o** la cuota"): el texto nuevo solo aplica a fallos futuros.
- El payload de Google se vuelca como `repr` de diccionario Python, con llaves sin cerrar por truncado.
- Fuentes de 12 px en la barra lateral (`assemblyai`, `gemini-flash-latest`) y en las marcas de tiempo dentro de los apuntes.
- Cada nota abierta inyecta un segundo `<h1>` en el documento.
- Sin overflow horizontal en móvil en ninguno de los estados medidos (375 px): la preocupación por los bloques de código no se confirmó a nivel de página; desplazan dentro de su propia caja.
- Los emoji 🔄 (Actualizar) y 🔁 (Reintentar) son la misma flecha circular a tamaño de botón.
- El enlace compartido se genera con `localhost`, sin aviso de que no funciona en otro dispositivo, y sin botón de copiar.
- El fallback de permiso cae en la opción más permisiva ante un valor inesperado.
- En la vista compartida la barra lateral se renderiza igual, dejando una columna muerta de ~190 px.
- Todos los botones de la app miden 40 px de alto (altura por defecto del tema), por debajo de los 44 px de objetivo táctil.

**Falsos positivos descartados:** el aviso amarillo de "Nueva clase" salió con ratio 1.25:1 en una primera medición por no componer el alfa del fondo; el valor real es ~10.7:1. Los tokens tipográficos de las fórmulas y las casillas deshabilitadas de las listas de tareas se excluyeron por no ser texto ni controles dirigidos al usuario.

## Questions to Consider

1. Si el éxito es "estudiar sin volver al audio, y si vuelves, la marca de tiempo te lleva al minuto exacto", ¿por qué la app no guarda ni reproduce el audio?
2. El grabador integrado no sirve para el caso de uso que define el producto, y la propia pantalla lo dice. ¿Por qué sigue siendo el modo por defecto?
3. Si el alcance confirmado es público, ¿a quién le resuelve algo "prueba con otro modelo en `GEMINI_MODEL`"?
4. Con la traducción dentro del alcance y el idioma decidiéndose en cada clase, ¿dónde va a caber ese selector?
5. Si cinco clases ya son indistinguibles, ¿qué pasa a las cincuenta? ¿La lista plana es la estructura correcta, o Mis clases debería vivir dentro de Grupos?
6. El comentario de `config.toml` dice que los apuntes se pasan a serif desde el CSS de la app, y ese CSS no existe. ¿Cuántas otras decisiones documentadas como hechas están solo en los comentarios?
