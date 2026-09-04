---
target: frontend/app.py
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 4
timestamp: 2026-09-03T17-15-30Z
slug: frontend-app-py
---
Method: dual-agent (A: revisión de diseño · B: detector + evidencia de navegador)

## Design Health Score

| # | Heurística | Pts | Antes | Problema clave |
|---|---|---|---|---|
| 1 | Visibilidad del estado | 2 | 2 | Al desplegar una clase completada la caja queda abierta y **vacía 14 s**, sin spinner: `_leer` usa `show_spinner=False` |
| 2 | Correspondencia con el mundo real | 3 | 3 | El uploader habla inglés ("Drag and drop file here", "Limit 1GB per file") dentro de una app en español |
| 3 | Control y libertad | **2** | 1 | Mejora: las cinco acciones destructivas ya confirman. Queda que la transcripción es un `text_area` editable sin guardado |
| 4 | Consistencia y estándares | 2 | 2 | Dos `st.radio` horizontales idénticos y contiguos hacen cosas de rango distinto: navegación global y modo de carga |
| 5 | Prevención de errores | 2 | 2 | "Adjuntar" sin PDF no hace nada: ni error ni mensaje. Igual "Añadir nota" y "Añadir tema" |
| 6 | Reconocer antes que recordar | 1 | 2 | Cinco filas y cuatro se llaman `clase_larga.wav`; desde Grupos no se llega a los apuntes |
| 7 | Flexibilidad y eficiencia | 1 | 2 | Sin búsqueda, filtro, orden ni renombrar. El enlace va en un `text_input` sin botón de copiar |
| 8 | Estética y minimalismo | 2 | 2 | Dos `st.metric` gigantes ocupan 461 px de 812 en móvil antes de que empiecen los apuntes |
| 9 | Recuperación de errores | **3** | 2 | Mejora: el 404 ya no se anuncia como caída de red y la clase fallida enseña su transcripción |
| 10 | Ayuda y documentación | 2 | 3 | La carga instructiva vive en captions al 0.6 de opacidad, el texto más tenue de la pantalla |
| **Total** | | **20/40** | 21/40 | |

Máximo aplicable: 40; las diez heurísticas aplican.

**La comparación no es limpia y conviene decirlo.** Dos heurísticas subieron por arreglos reales (control y libertad, recuperación de errores) y tres bajaron sobre problemas que ya existían y no toqué (identificar clases, búsqueda, ayuda). Son dos jueces distintos puntuando el mismo tipo de defecto con distinta dureza, así que el 21 → 20 mide sobre todo eso, no un empeoramiento.

## Design Specificity Verdict

**Evaluación de diseño:** anclado en este producto, con diferencia. El panel de una clase fallida antepone "Tu transcripción está guardada y no se perdió" al botón de reintento; eso solo tiene sentido si transcribir cuesta dinero. El vocabulario es de cursada real: materia, unidad, práctico, cátedra, comisión. Pero la especificidad se detiene en el copy y no llega a la estructura: lista plana, cinco solapas y navegación por radio son lo que pondría cualquier CRUD.

**Escaneo determinista:** `detect.mjs --json frontend` devolvió `[]` con salida 0; repetido sobre la raíz, idéntico. El proyecto no tiene ficheros de marcado ni estilo propios, así que no hay nada que escanear.

**Superposiciones visuales:** no hubo inyección de `detect.js`, así que no existe ninguna superposición visible en el navegador. La evidencia dura viene de mediciones directas del DOM.

## Overall Impression

Los cuatro P1 anteriores están cerrados y verificados. Lo que queda son problemas más profundos que ninguno de los arreglos tocaba: identificar una clase entre cinco iguales, llegar a los apuntes desde la materia, y un enlace de compartir que no funciona fuera de este ordenador. La mayor oportunidad ya no es visual: es que la lista de clases se pueda leer.

## What's Working

1. **El panel de clase fallida es diseño de producto.** Orden: qué pasó, con el texto del proveedor intacto → qué sobrevivió → qué puedes hacer. Invierte el reflejo de esconder los fallos.
2. **La columna de lectura está medida y acierta.** 528 px de caja; el resto de la interfaz sigue a ancho completo, así que el bloque estrecho se lee como lo que es.
3. **La navegación fuera de la barra lateral.** Verificado en móvil: las tres secciones visibles y tocables sin descubrir el chevron de 32 px con `aria-label` vacío.
4. **La confirmación destructiva en dos tiempos**, con el aviso encima del botón y la acción nombrada en el botón.

## Priority Issues

### [P0] El enlace para compartir es `localhost` y nada lo advierte
El campo muestra `http://localhost:8501?grupo=…`. `enlace_base()` solo usa la IP real si alguien definió `APP_URL`, y ningún texto dice si el enlace funciona. Se entrega un artefacto roto presentado como listo para enviar, y el fallo es diferido y silencioso: le ocurre al destinatario.
**Arreglo:** si el host es `localhost`, no mostrar un enlace enviable: avisar de que solo funciona en este ordenador y cómo arrancar con `APP_URL`. Y cambiar el `text_input` por `st.code(url)`, que trae botón de copiar.
**Comando:** `/impeccable harden`

### [P1] "Mis clases" no identifica nada, y desde Grupos no se llega a los apuntes
Cinco filas, cuatro con el mismo nombre, sin materia, sin duración, sin palabra de estado (solo ✅/❌, que un lector de pantalla lee como "cross mark"). La solapa Clases de un grupo escribe la clase como texto muerto y remata mandándote a buscarla de memoria.
**Arreglo:** cabecera con estado en palabras, materia y duración; permitir renombrar; y que la clase del grupo sea un botón que abra sus apuntes.
**Comando:** `/impeccable layout`

### [P1] Cambiar el permiso del enlace no hace nada, y la pantalla se contradice
Verificado en vivo: con el radio en "Leer y escribir", el caption seguía diciendo "el enlace da acceso de solo lectura". El cambio no se aplica hasta pulsar "Actualizar enlace" y nada lo indica. El caso peligroso es el inverso: bajar de escritura a lectura, verlo marcado, e irse con el acceso de escritura intacto.
**Por qué importa:** privado por defecto es la única restricción que PRODUCT.md marca como innegociable. Y "Actualizar enlace" corta a todos los que ya lo tenían, sin confirmación, mientras borrar una nota de tres líneas exige dos clics: el modelo de riesgo está invertido.
**Comando:** `/impeccable harden`

### [P1] Las marcas de tiempo no llevan a ningún sitio
Verificado: cero elementos `<audio>` en la vista de clase. PRODUCT.md define el éxito como "cuando necesite volver, la marca de tiempo lo lleve al minuto exacto". Esa mitad no tiene interfaz, y el audio no se ofrece ni para descargar.
**Comando:** `/impeccable shape`

### [P1] Al desplegar una clase terminada, 14 segundos de caja vacía
`mostrar_apuntes` pide el detalle completo de forma síncrona y `_leer` lleva `show_spinner=False`, así que no aparece ni el spinner por defecto. Además el cuerpo de los expanders colapsados también se ejecuta, así que cada recarga vuelve a pedir el detalle de todas las clases.
**Comando:** `/impeccable optimize`

### [P2] La solapa activa es el texto menos legible de la página
Medido por las dos evaluaciones: `#1F6FA8` sobre el lienzo da **3.41:1** a 14 px, por debajo del 4.5:1 de AA. Las inactivas están a 15:1.
**Y no se puede resolver con el tema:** `primaryColor` es un único token que hace de fondo de botón primario (necesita ser oscuro para el texto blanco) y de color de texto de la solapa activa (necesitaría ser claro). Calculado: con este lienzo la ventana entre ambas restricciones está **vacía**, y el mejor compromiso posible da 4.29:1 en los dos usos, todavía corto.
**Comando:** `/impeccable colorize`

## Persona Red Flags

**Usuario móvil distraído:** el texto más prominente de la pantalla del teléfono dice **"Deploy"**; la marca del producto vive entera en la barra colapsada. La sección activa se distingue solo por un punto de 8 px. Las etiquetas de navegación miden 26 px de alto. De la cabecera de una clase a la primera línea de apuntes hay 461 px de 812.

**Usuario dependiente de accesibilidad:** `lang="es"` funciona, verificado. Pero el estado de cada clase es solo un emoji; la estructura de encabezados mete un `<h1>` de los apuntes dentro del `<h2>` de la sección; la solapa activa falla el contraste; y el `text_area` de la transcripción es editable sin guardado, así que quien tabule dentro escribe y lo pierde.

**Primerizo confundido:** dos grupos de radio idénticos y contiguos sin forma de saber cuál manda. Y **dos estados vacíos siguen diciendo "en el menú de la izquierda"** cuando la navegación ya está arriba.

**Estudiante que graba cuatro horas:** el modo por defecto sigue siendo la grabadora que la propia app declara incapaz de sostener su clase. Sube 700 MB contra un spinner sin bytes ni ETA, y si toca otro radio mata la subida. Tres de sus cinco clases fallaron por cuota y ninguna pantalla se lo dice de forma agregada: puede encolar cuatro horas y esperar media hora para el cuarto fallo.

## Minor Observations

- Las fórmulas salen como bloque de código en vez de LaTeX, y las variables sueltas como chips naranjas a mitad de frase.
- "Tareas y lecturas pendientes" pinta casillas que no se pueden marcar: afordancia falsa.
- Un PDF adjuntado no se puede volver a descargar.
- Los apuntes de dos clases generan anclas duplicadas en el DOM.
- Las cuatro solapas ocultas de un grupo se ejecutan igual en cada pasada.
- Medida de lectura: las tres mediciones dan 66, 73 y 80 caracteres por línea sobre la misma caja de 528 px, según el ancho medio de carácter que asuma cada método. Estamos en el borde alto del rango 65-75, no cómodamente dentro.

## Questions to Consider

1. ¿Por qué "Grabar ahora" sigue siendo el modo por defecto de una app cuyo escenario principal son clases de 2 a 4 horas y cuya propia grabadora está documentada como incapaz de sostenerlas?
2. Borrar una nota de tres líneas exige dos clics; publicar los apuntes de una materia en un enlace anónimo exige uno. ¿Cuál es realmente irreversible?
3. Si el audio no se conserva, ¿no debería la clase decirlo antes de que el estudiante borre el original de su teléfono?
4. Cuatro de cinco clases se llaman igual y no hay forma de renombrarlas. ¿Cómo se ve esta pantalla en la semana 14 con seis materias?
5. La app sabe en cada error que la cuota está agotada. ¿Por qué deja encolar cuatro horas sin decirlo?
6. Streamlit no permite que `primaryColor` sea a la vez fondo oscuro de botón y texto claro de solapa. ¿Cuánto más va a costar cada arreglo visual antes de que el techo del framework deje de compensar?
