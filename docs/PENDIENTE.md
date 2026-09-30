# Qué queda por hacer

Ordenado por lo que desbloquea, no por lo que cuesta. Cada punto dice **qué**,
**por qué** y **qué hay que decidir antes de empezar**, porque lo que más tiempo
cuesta después es reconstruir el porqué.

Actualizado el 2026-09-30: entrar con Google ya funciona en el navegador y el
`state` está atado al navegador que lo pidió. No funcionaba, aunque aquí ponía
que sí; lo que pasaba está en [`ESTADO.md`](ESTADO.md).

El 2026-09-06 salieron de esta lista otros cuatro puntos, también contados en
[`ESTADO.md`](ESTADO.md): que la sesión no sobrevivía a recargar la página, la
traducción de los apuntes, la medición del consumo por cuenta, y la grabadora
que sube la clase mientras se graba.

De la grabadora queda **grabar desde el teléfono**, que no es un fleco sino un
cambio en cómo se sirve la app: los pasos exactos están en
[`movil.md`](movil.md). No se hizo porque tocar el modo `--red` sin un móvil
delante sería escribir a ciegas en la única vía que hoy funciona.

Del consumo queda lo que no es código: **el modelo de cobro**. Los números ya se
guardan; qué se cobra por ellos y cuánto es una decisión de negocio. Y una
salvedad: el libro empieza hoy, así que las clases que ya existían aparecen con
cero. Se decidió no rellenarlas hacia atrás porque solo se puede reconstruir la
mitad —los minutos de audio— y no lo que costó redactar sus apuntes; un total a
medias engaña más que un cero.

De la traducción queda una decisión abierta, apuntada en
[`../PRODUCT.md`](../PRODUCT.md): si además se ofrece la **transcripción**
traducida. Hoy no se traduce nunca, que es el comportamiento correcto por
defecto; ofrecerla sería añadir algo, no cambiar lo hecho. El estado de lo que ya funciona está en
[`ESTADO.md`](ESTADO.md); el contexto de producto, en [`../PRODUCT.md`](../PRODUCT.md).

---

## 1. Entrar con Google

**Estado.** Implementado, probado en Chromium hasta el servidor de tokens de
Google, e **inactivo**: la opción no aparece hasta que existan
`GOOGLE_CLIENT_ID` y `GOOGLE_CLIENT_SECRET`.

Hasta el 30/09 aquí ponía «implementado y probado». No lo estaba: ningún test lo
había encendido, y encendido tiraba la app al pulsar el botón. Arreglado, junto
con el `state` que no estaba atado al navegador; los detalles, en
[`ESTADO.md`](ESTADO.md).

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
`run.py --configurar`. Es media hora de consola. En la consola, la *URI de
redirección autorizada* tiene que ser exactamente la de `APP_URL` (por defecto
`http://localhost:8501`), sin barra final: es la que la app manda a Google.

Después, **entrar una vez con una cuenta real** para cerrar lo único que no se
pudo probar sin credenciales: el canje del código de verdad y la creación de la
cuenta a partir de lo que devuelve Google.

## 2. Recuperar la contraseña

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

- Un detalle que no tiene arreglo limpio: el campo de nombre de clase muestra
  «Press Enter to apply», en inglés, porque lo pone Streamlit. Ocultarlo se
  llevaría por delante el contador de caracteres, que sí sirve.
