# Qué queda por hacer

Ordenado por lo que desbloquea, no por lo que cuesta. Cada punto dice **qué**,
**por qué** y **qué hay que decidir antes de empezar**, porque lo que más tiempo
cuesta después es reconstruir el porqué.

Actualizado el 2026-09-06. Tres puntos salieron hoy de esta lista y están
contados en [`ESTADO.md`](ESTADO.md): que la sesión no sobrevivía a recargar la
página, la traducción de los apuntes, y la medición del consumo por cuenta.

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

## 1. La grabadora integrada no aguanta una clase

**Qué pasa hoy.** Guarda sin comprimir y no envía nada hasta que se para. Una
clase de 4 h son unos 2,5 GB en la memoria del navegador. Solo sirve para
pruebas cortas, y la propia interfaz lo avisa.

**Por qué importa.** Es el escenario «durante la clase» de `PRODUCT.md`: dejar el
teléfono grabando. Hoy hay que grabar con la app del móvil y subir el fichero.

**Qué haría falta:** grabar en trozos y subirlos según se generan, con
`MediaRecorder` y un endpoint que reciba partes. Es un componente propio de
Streamlit, no un widget de los que trae.

## 2. Entrar con Google

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

**Ya no está esperando a nada.** Lo que lo frenaba era que sin cookies no se
podía atar el `state` de Google a un navegador concreto: alguien podía fabricar
un enlace que te metiera en *su* cuenta, y las clases que subieras irían a parar
ahí. Ahora hay cookies, así que al encender Google conviene aprovecharlas para
eso. El `state` sigue siendo de un solo uso y lo guarda el backend, que es lo
que impide reutilizar un código de otro flujo.

## 3. Recuperar la contraseña

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
