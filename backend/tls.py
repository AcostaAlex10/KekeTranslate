"""Hace que Python confie en los certificados del sistema operativo.

Por que hace falta: los antivirus con "escaneo SSL" (Norton, Kaspersky, ESET,
Avast) y los proxies corporativos **interceptan el trafico HTTPS**. Sustituyen
el certificado del servidor por uno propio, firmado por una CA que instalan en
el almacen de Windows.

Los navegadores lo aceptan porque consultan ese almacen. Python no: usa la
lista fija del paquete `certifi`, donde esa CA no esta. Resultado:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

...en **todas** las llamadas a AssemblyAI, Anthropic o Gemini, aunque las
claves sean correctas. El sintoma despista mucho, porque el navegador entra sin
problema a las mismas paginas.

`truststore` redirige la verificacion al almacen del sistema. No desactiva
nada: se sigue validando la cadena, solo que contra la lista que el sistema
considera de confianza.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def usar_certificados_del_sistema() -> bool:
    """Activa la verificacion contra el almacen del sistema. Devuelve si pudo.

    Nunca lanza: si `truststore` no esta instalado o falla, la aplicacion
    arranca igual con el comportamiento por defecto de Python.
    """
    try:
        import truststore
    except ImportError:
        logger.debug(
            "truststore no esta instalado; se usa la lista de certificados de "
            "Python. Si tu antivirus inspecciona el trafico HTTPS, las llamadas "
            "a las APIs pueden fallar con CERTIFICATE_VERIFY_FAILED."
        )
        return False

    try:
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 - nunca debe impedir el arranque
        logger.warning("No se pudo activar truststore", exc_info=True)
        return False

    logger.info("Certificados: se usa el almacen del sistema (truststore)")
    return True
