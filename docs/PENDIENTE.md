# Qué queda por hacer

Ordenado por lo que desbloquea, no por lo que cuesta. Cada punto dice **qué**,
**por qué** y **qué hay que decidir antes de empezar**, porque lo que más tiempo
cuesta después es reconstruir el porqué.

Actualizado el 2026-09-04. El estado de lo que ya funciona está en
[`ESTADO.md`](ESTADO.md); el contexto de producto, en [`../PRODUCT.md`](../PRODUCT.md).

---

## 1. La sesión no sobrevive a recargar la página

**Qué pasa hoy.** Se entra con correo y contraseña, y funciona. Pero el testigo
de sesión vive en `st.session_state`, que Streamlit tira al recargar. Cada F5
devuelve a la pantalla de entrar.

**Por qué está sin resolver.** Streamlit 1.41 no expone cookies. Las tres
salidas, y por qué ninguna es gratis:

| Salida | A favor | En contra |
|---|---|---|
| Testigo en la URL | No hace falta nada | Queda en el historial y en cualquier enlace copiado. Descartado. |
| Componente de cookies de terceros | Resuelve hoy | La puerta de entrada queda colgando de un paquete ajeno con fama de caprichoso |
| Subir Streamlit a 1.42+ | `st.context.cookies` es de la casa | Hay que revalidar los enganches de CSS (`st-key-…`, `data-testid`) de los que depende la interfaz |

**Recomendado:** probar 1.42+ en una rama, correr los 186 tests y revisar a ojo
el contraste del selector y la alineación de las listas. Si aguanta, es la vía
buena. Si no, componente de terceros **como mejora**, nunca como requisito: si
falla, se debe volver a pedir la contraseña, no quedarse fuera.

Resolver esto además cierra el hueco del punto 5: sin cookies no se puede atar
el `state` de Google a un navegador concreto.

## 2. Traducir, con el idioma elegido en cada clase

**Qué falta.** Hoy el idioma es un ajuste global del `.env` y los apuntes salen
en el idioma de la clase. Está decidido que el idioma de salida se elige **por
clase**, no una vez para todo.

**Por qué importa.** Es el nombre del producto. KekeTranslate no traduce nada.

**Sin decidir, y hay que decidirlo antes de tocar código:** si al traducir se
conserva además la transcripción en el idioma original. Cambia el modelo de
datos: o un campo más en `Job`, o dos trabajos enlazados.

## 3. La grabadora integrada no aguanta una clase

**Qué pasa hoy.** Guarda sin comprimir y no envía nada hasta que se para. Una
clase de 4 h son unos 2,5 GB en la memoria del navegador. Solo sirve para
pruebas cortas, y la propia interfaz lo avisa.

**Por qué importa.** Es el escenario «durante la clase» de `PRODUCT.md`: dejar el
teléfono grabando. Hoy hay que grabar con la app del móvil y subir el fichero.

**Qué haría falta:** grabar en trozos y subirlos según se generan, con
`MediaRecorder` y un endpoint que reciba partes. Es un componente propio de
Streamlit, no un widget de los que trae.

## 4. Medir el consumo por persona

**Por qué ahora.** Está decidido que se va a cobrar. Con las cuentas ya se sabe
de quién es cada clase, que era el requisito previo; falta contar los minutos
transcritos y los caracteres generados por cuenta.

**Ojo:** es más fácil hacerlo ahora, mientras hay una sola cuenta y ninguna
factura, que cuando haya que reconstruirlo hacia atrás.

## 5. Entrar con Google

**Estado.** Implementado y probado, pero **inactivo**: la opción no aparece
hasta que existan `GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`.

**No está bloqueado por nada.** Se llegó a anotar que la prueba gratuita de 90
días de Google Cloud lo impedía. Es falso, y conviene dejarlo escrito para no
volver a perder tiempo buscando alternativas:

- Crear el proyecto y las credenciales OAuth 2.0 **no exige cuenta de
  facturación**. Los 300 USD / 90 días son para recursos de pago —máquinas,
  bases—, no para identidad. Es gratis y no caduca.
- Los scopes que pide la app, `openid email profile`, son **no sensibles**, así
  que publicarla **no exige el proceso de verificación** de Google, que es lo
  que suele asustar.
- La app no pide `access_type=offline` ni usa refresh tokens: hace un solo
  intercambio del código por el `id_token`. Así que la caducidad de 7 días de
  los refresh tokens en modo *Testing* no la afecta. Aun así conviene publicar
  la app, porque en Testing hay tope de 100 usuarios.

**Lo que hay que hacer, entonces:** crear el proyecto, configurar el consent
screen, sacar client ID y secreto, y ponerlos en el `.env` con
`run.py --configurar`. Es media hora de consola, no desarrollo.

**Pero conviene hacer antes el punto 1.** El `state` es de un solo uso y lo
guarda el backend, lo que impide reutilizar un código de otro flujo. Lo que
**no** impide, por no haber cookies, es atar ese `state` a un navegador
concreto: alguien podría fabricar un enlace que te meta en *su* cuenta, y las
clases que subieras irían a parar ahí. Con correo y contraseña ese riesgo no
existe, así que encender Google antes de resolver las cookies añade una vía de
entrada peor que la que ya hay.

## 6. Recuperar la contraseña

No existe, porque no hay envío de correo. Con una cuenta no importa; con dos, sí.
Necesita un servicio de correo, que es otra dependencia externa y otra clave que
rotar. Resend, Brevo o Mailgun tienen tier gratuito suficiente para esto;
[free-for.dev](https://free-for.dev) los lista y compara.

---

## Verificaciones que nunca se hicieron

Ninguna es desarrollo; todas son media hora y cierran una duda.

- **Una clase real de 4 horas**, de punta a punta. Lo más largo probado son 4,5
  minutos. Es donde aparecerían los límites de tiempo, de memoria y de troceado.
- **Grabar desde el móvil.** Necesita `python run.py --red` y abrir el 8501 en el
  firewall de Windows, que pide permisos de administrador.
- **El anotador de Claude** contra su API real. Solo se ha usado Gemini.
- **Un PDF de verdad de la cátedra**, no el de prueba de 3 páginas.

## Higiene

- `/impeccable polish`: es el paso que el propio skill pide después de `layout`.
  Cerraría estados sueltos —vacíos, foco de teclado, la lista con 200 clases.
- Un detalle que no tiene arreglo limpio: el campo de nombre de clase muestra
  «Press Enter to apply», en inglés, porque lo pone Streamlit. Ocultarlo se
  llevaría por delante el contador de caracteres, que sí sirve.
