# Product

<!-- impeccable:product-schema 1 -->

Los encabezados van en inglés porque los lee la herramienta; el contenido va en
español, como el resto de la documentación del proyecto.

## Platform

web

## Users

Estudiantes universitarios que cursan clases largas, de **2 a 4 horas**, y
necesitan apuntes utilizables de lo que se dijo.

El producto nació para un estudiante de la Facultad de Ingeniería (UNaM) y su
comisión, pero **el alcance confirmado es público**: cualquier persona fuera de
esa facultad es usuario objetivo. Eso convierte en obligatorias cosas que hoy no
existen: cuentas de usuario, aislamiento de datos entre personas y control del
coste por usuario.

Dos situaciones de uso distintas y ambas reales:

- **Durante la clase.** El teléfono queda apoyado cerca de quien habla y graba.
  Nadie está mirando la pantalla mientras tanto.
- **Después.** La grabación (propia o ya existente) se sube, se procesa sin
  supervisión, y los apuntes se leen más tarde, normalmente en un ordenador.

## Product Purpose

Convertir la grabación de una clase larga en apuntes estructurados y fiables:
resumen, temas con su marca de tiempo, desarrollo detallado, tareas con su fecha
de entrega, advertencias del docente y glosario.

El éxito es que alguien pueda estudiar de esos apuntes **sin volver al audio**.
Las marcas de tiempo sirven para situarse dentro de la transcripción, que es lo
que queda de la clase: el audio no se guarda.

## Positioning

Tres cosas que un transcriptor genérico no hace:

1. **Procesa la clase entera.** Cuatro horas entran completas, sin trocear y sin
   perder la visión de conjunto.
2. **Lee el material de la materia.** El programa, los prácticos y los apuntes
   del docente se adjuntan al grupo y entran en el prompt: los apuntes pueden
   situar la clase dentro de la planificación y usar la terminología de la
   cátedra. El material es apoyo, nunca fuente: si la clase contradice al
   documento, manda la clase.
3. **Devuelve estructura académica, no un muro de texto.** Distingue una
   advertencia de examen de una definición, y una tarea con fecha de un
   comentario al pasar.

## Operating Context

- Las clases son presenciales y de una sola voz dominante (el docente), con
  intervenciones sueltas del alumnado.
- La organización real del estudiante es por **materia**, subdividida en
  **temas** o unidades. Los grupos de la app reproducen esa estructura.
- Los documentos que circulan en una cursada son PDF: programa, guías de
  trabajos prácticos y apuntes del docente.
- Compartir apuntes con compañeros es una práctica habitual, pero **selectiva**:
  se comparte lo que uno decide, con quien uno decide.
- El procesado es largo (10–30 min para una clase de 4 h) y ocurre sin nadie
  mirando; la persona vuelve más tarde.

## Capabilities and Constraints

**Confirmado y funcionando**

- Transcripción con identificación de oradores y marcas de tiempo.
- Apuntes generados por IA en Markdown, con fórmulas en LaTeX.
- Se acepta audio y vídeo; del vídeo solo se aprovecha el sonido. **La app nunca
  graba vídeo**, solo audio.
- Grupos por materia, con temas, PDFs leídos por la IA y notas propias.
- Los apuntes generados se pueden editar a mano; la versión corregida se guarda
  aparte de la de la IA y sobrevive a rehacer los apuntes.
- Reintento de la anotación reutilizando la transcripción ya pagada.
- Enlace para compartir un grupo, con permiso de lectura o de escritura a
  elección de quien lo creó, revocable.

**Restricciones**

- **Los apuntes son privados por defecto.** Nada se comparte salvo decisión
  explícita de quien los generó. Es la única restricción marcada como
  innegociable, y desde que hay cuentas está respaldada por el código: la API
  niega por defecto y solo abre lo que está declarado abierto.
- **Hay cuentas y la API está cerrada.** Se entra con correo y contraseña o
  con Google, y cada persona solo ve lo suyo: pedir la clase de otro devuelve
  404, no 403. Lo único accesible sin cuenta es un enlace compartido, y solo
  abre el grupo al que apunta. La sesión, en cambio, **vive mientras la pestaña
  siga abierta**: al recargar hay que volver a entrar, porque Streamlit no
  ofrece cookies y no se quiso meter el testigo en la URL.
- **El audio se borra en cuanto la clase se transcribe bien**, y no hay
  reproductor. Es una decisión tomada, no una carencia: guardar el audio de una
  cursada entera cuesta disco de verdad —una clase de 4 h son 2,5 GB sin
  comprimir, unos 58 MB recomprimida a Opus— y el producto se apoya en que los
  apuntes basten. Del audio de una clase fallida sí se conserva la
  transcripción, que es la parte cara.
- **La grabadora integrada es provisional**: guarda sin comprimir y no envía
  nada hasta que se detiene, así que no sostiene una clase de varias horas.
- El nivel gratuito del anotador **no entrega generaciones largas** cuando se
  agota la cuota diaria; está medido y documentado en `docs/ESTADO.md`.

**Decisiones tomadas, todavía sin implementar**

- **Traducir es parte del producto.** El nombre no es histórico: se contempla
  cursar en un idioma y recibir los apuntes en otro. El **idioma de salida se
  elige en cada clase**, no una vez para todo. Hoy el idioma es un ajuste global
  del `.env` y los apuntes salen en el idioma de la clase.
- **Se contempla cobrar** por el uso. Con las cuentas ya se sabe de quién es
  cada clase, que era el requisito previo; falta medir el consumo.

**Sin decidir**

- Si al traducir se conserva además la transcripción en el idioma original.
- El modelo concreto de cobro y cuándo llegan las cuentas de usuario.
- Qué pasa con las clases de varias voces simultáneas o con audio malo.

## Brand Commitments

- El nombre es **KekeTranslate** y se mantiene: con la traducción dentro del
  alcance, el nombre describe el producto.
- La interfaz y los apuntes están hoy en español. **No es una restricción**: el
  alcance público y la traducción implican que habrá otros idiomas.
- Voz: directa y sin adornos. Los mensajes de error dicen qué pasó y qué hacer,
  y conservan el texto original del proveedor cuando ahí está el dato que
  resuelve el problema.

## Evidence on Hand

Real y verificable dentro del repositorio:

- Apuntes generados en una ejecución real de punta a punta con servicios de
  verdad, con fórmulas, advertencia de examen, preguntas del alumnado, tareas
  con fecha y glosario.
- `docs/ESTADO.md`: estado del proyecto, decisiones tomadas y sus motivos, y los
  fallos encontrados al probar contra las APIs reales.
- `README.md`: instalación, uso y tabla de endpoints.
- 131 tests automatizados.

Lo que **no** existe y no debe darse por supuesto: usuarios reales más allá del
autor, mediciones de precisión de la transcripción, comparativas con otras
herramientas, testimonios, precios y cualquier compromiso de disponibilidad.

## Product Principles

1. **La transcripción es lo caro; nunca se tira.** Si falla la generación de
   apuntes, la transcripción se conserva y se reintenta solo esa parte. Ningún
   fallo del modelo puede obligar a volver a subir y volver a pagar la clase.
2. **El material de la materia es apoyo, no fuente.** Los apuntes salen de lo
   que se dijo en clase. Nunca se añade contenido del programa que el docente no
   llegó a explicar, y ante una contradicción manda la clase.
3. **Compartir es siempre un acto explícito.** Lo privado por defecto no admite
   excepciones cómodas.
4. **Reorganizar no destruye.** Borrar un grupo o un tema no se lleva por delante
   las clases transcritas ni el material: cuestan dinero y una espera larga.
5. **Un error debe decir qué hacer.** Cuando el fallo viene de fuera, se traduce
   a algo accionable sin ocultar el mensaje original.
