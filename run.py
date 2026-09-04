"""Arranca KekeTranslate completo con un solo comando.

    python run.py            # uso normal, solo en esta maquina
    python run.py --red      # accesible desde el movil (HTTPS, ver docs/movil.md)

Levanta el backend y el frontend a la vez, espera a que respondan, abre el
navegador y se encarga de cerrar los dos procesos al salir con Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PUERTO_BACKEND = 8000
PUERTO_FRONTEND = 8501

PROVEEDORES_TRANSCRIPCION = {
    "assemblyai": "credito gratis al registrarte, identifica oradores",
    "deepgram": "mas credito gratis, identifica oradores",
    "openai": "de pago, sin oradores, necesita ffmpeg",
}
PROVEEDORES_ANOTADOR = {
    "gemini": "tiene nivel gratuito",
    "anthropic": "Claude, de pago",
}
CLAVE_DE_TRANSCRIPCION = {
    "assemblyai": "ASSEMBLYAI_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
}
CLAVE_DE_ANOTADOR = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


# ---------------------------------------------------------------------------
# Comprobaciones previas
# ---------------------------------------------------------------------------


def piso(mensaje: str) -> None:
    print(f"  {mensaje}")


def python_del_entorno() -> Path:
    """Devuelve el interprete del entorno virtual, o el actual si no lo hay."""
    candidato = RAIZ / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )
    return candidato if candidato.exists() else Path(sys.executable)


def comprobar_dependencias(python: Path) -> bool:
    """Verifica que estan instaladas las librerias imprescindibles."""
    faltan = subprocess.run(
        [str(python), "-c", "import fastapi, uvicorn, streamlit, anthropic, pypdf"],
        capture_output=True,
    ).returncode
    if faltan:
        print("\n[X] Faltan dependencias. Instalalas con:\n")
        piso(f'"{python}" -m pip install -r requirements.txt')
        print()
        return False
    return True


def preparar_env() -> None:
    """Crea el `.env` a partir del ejemplo la primera vez."""
    env = RAIZ / ".env"
    ejemplo = RAIZ / ".env.example"
    if not env.exists() and ejemplo.exists():
        shutil.copy(ejemplo, env)
        piso(f"Creado {env.name} a partir de {ejemplo.name}")


def leer_env() -> dict[str, str]:
    """Lee el `.env` en un diccionario simple, con el entorno por encima."""
    valores: dict[str, str] = {}
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            nombre, valor = linea.split("=", 1)
            # El nombre se normaliza a mayusculas: un `.env` escrito a mano
            # puede traer `Gemini_API_KEY`, y tratarlo como una variable
            # distinta de `GEMINI_API_KEY` acabaria duplicando la linea.
            valores[nombre.strip().upper()] = valor.strip().strip("\"'")

    for nombre in list(valores) + [
        "TRANSCRIPTION_PROVIDER", "ANNOTATOR_PROVIDER",
        "ASSEMBLYAI_API_KEY", "DEEPGRAM_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    ]:
        if os.environ.get(nombre):
            valores[nombre] = os.environ[nombre]
    return valores


def claves_que_faltan() -> list[str]:
    """Claves sin rellenar de los proveedores elegidos, sin mostrar su valor.

    Se mira solo lo que hace falta: quien use Gemini no necesita la clave de
    Anthropic, y avisarle de que le falta solo confunde.
    """
    env = leer_env()

    clave_transcripcion = CLAVE_DE_TRANSCRIPCION.get(
        env.get("TRANSCRIPTION_PROVIDER", "assemblyai"), "ASSEMBLYAI_API_KEY"
    )
    clave_anotador = CLAVE_DE_ANOTADOR.get(
        env.get("ANNOTATOR_PROVIDER", "gemini"), "GEMINI_API_KEY"
    )

    return [
        nombre
        for nombre in (clave_transcripcion, clave_anotador)
        if not env.get(nombre)
    ]


def escribir_en_env(nuevos: dict[str, str]) -> None:
    """Actualiza claves en el `.env` conservando el resto del fichero."""
    env = RAIZ / ".env"
    lineas = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    pendientes = {nombre.upper(): valor for nombre, valor in nuevos.items()}

    salida = []
    ya_escritas: set[str] = set()
    for linea in lineas:
        nombre = linea.split("=", 1)[0].strip().upper() if "=" in linea else ""
        if not nombre:
            salida.append(linea)
            continue
        # Una variable repetida (normalmente por diferencias de mayusculas) se
        # deja una sola vez: dos lineas con el mismo nombre son una trampa,
        # porque editar la equivocada no tiene ningun efecto visible.
        if nombre in ya_escritas:
            continue
        ya_escritas.add(nombre)
        if nombre in pendientes:
            salida.append(f"{nombre}={pendientes.pop(nombre)}")
        else:
            salida.append(f"{nombre}={linea.split('=', 1)[1].strip()}")

    for nombre, valor in pendientes.items():
        salida.append(f"{nombre}={valor}")

    env.write_text("\n".join(salida) + "\n", encoding="utf-8")


def reparar_env() -> list[str]:
    """Corrige un `.env` donde una clave acabo en el campo del proveedor.

    Es un error facil de cometer: quien viene a poner sus claves pega la
    primera en cuanto ve un hueco, aunque lo que se este preguntando sea otra
    cosa. El sintoma es peor que el error, porque un proveedor invalido impide
    arrancar el backend con un mensaje de validacion que no explica nada.

    La clave no se muestra en ningun momento: solo se mueve de linea.
    """
    env = leer_env()
    correcciones: list[str] = []
    nuevos: dict[str, str] = {}

    for campo, validos, clave_destino, por_defecto in (
        ("TRANSCRIPTION_PROVIDER", PROVEEDORES_TRANSCRIPCION,
         "ASSEMBLYAI_API_KEY", "assemblyai"),
        ("ANNOTATOR_PROVIDER", PROVEEDORES_ANOTADOR,
         "GEMINI_API_KEY", "gemini"),
    ):
        valor = env.get(campo, "")
        if not valor or valor in validos:
            continue

        # Lo que hay no es un proveedor. Si parece una clave y su hueco esta
        # libre, se mueve alli en vez de tirarla.
        if len(valor) > 12 and len(env.get(clave_destino, "")) <= 1:
            nuevos[clave_destino] = valor
            correcciones.append(
                f"{campo} contenia una clave: movida a {clave_destino}"
            )
        else:
            correcciones.append(f"{campo} tenia un valor invalido: '{valor[:12]}...'")

        nuevos[campo] = por_defecto

    if nuevos:
        escribir_en_env(nuevos)
    return correcciones


def _preguntar_opcion(titulo: str, opciones: dict[str, str], actual: str) -> str:
    """Pide una opcion por numero. Nunca acepta texto libre.

    Se eligen por numero justamente para que sea imposible pegar aqui una
    clave por error.
    """
    print()
    piso(titulo)
    claves = list(opciones)
    for numero, nombre in enumerate(claves, start=1):
        marca = "*" if nombre == actual else " "
        piso(f"   {marca} {numero}) {nombre:12} {opciones[nombre]}")

    while True:
        respuesta = input(f"   Numero [{claves.index(actual) + 1}]: ").strip()
        if not respuesta:
            return actual
        if respuesta.isdigit() and 1 <= int(respuesta) <= len(claves):
            return claves[int(respuesta) - 1]
        piso("   Escribe solo el numero de una de las opciones.")


def configurar(avanzado: bool = False) -> int:
    """Pide las claves por teclado y las guarda en el `.env`.

    Se hace asi, y no editando el fichero a mano, porque el `.env` mezcla
    comentarios y variables y es facil dejar un espacio de mas o unas comillas
    que hacen que la clave no se lea y el fallo aparezca mucho despues.

    Lo que se teclea no se muestra en pantalla ni queda en el historial del
    terminal.
    """
    from getpass import getpass

    preparar_env()

    print("\n=== Configurar KekeTranslate ===\n")

    for correccion in reparar_env():
        piso(f"[corregido] {correccion}")

    env = leer_env()
    proveedor = env.get("TRANSCRIPTION_PROVIDER", "assemblyai")
    anotador = env.get("ANNOTATOR_PROVIDER", "gemini")

    if avanzado:
        proveedor = _preguntar_opcion(
            "Que servicio transcribe el audio:", PROVEEDORES_TRANSCRIPCION, proveedor
        )
        anotador = _preguntar_opcion(
            "Que modelo escribe los apuntes:", PROVEEDORES_ANOTADOR, anotador
        )

    clave_t = CLAVE_DE_TRANSCRIPCION[proveedor]
    clave_a = CLAVE_DE_ANOTADOR[anotador]

    nuevos = {"TRANSCRIPTION_PROVIDER": proveedor, "ANNOTATOR_PROVIDER": anotador}

    print()
    piso(f"Transcripcion: {proveedor}   |   Apuntes: {anotador}")
    if not avanzado:
        piso("(para cambiarlos: run.py --configurar --avanzado)")
    print()
    piso("Ahora las dos claves. Al pegar NO veras nada en pantalla, ni")
    piso("asteriscos: es normal, no es que no funcione. Pega y dale a Enter.")
    piso("Enter en blanco deja la que ya tengas.\n")

    for clave in (clave_t, clave_a):
        actual = env.get(clave, "")
        estado = f"ya hay una, termina en ...{actual[-4:]}" if len(actual) > 4 else "vacia"
        valor = getpass(f"   {clave} ({estado}): ").strip()
        if valor:
            nuevos[clave] = valor

    escribir_en_env(nuevos)

    print()
    piso(f"Guardado en {RAIZ / '.env'}")
    for nombre in (clave_t, clave_a):
        guardada = leer_env().get(nombre, "")
        # Se confirma que quedo escrita sin llegar a mostrarla entera.
        estado = f"OK (...{guardada[-4:]})" if guardada else "SIGUE VACIA"
        piso(f"  {nombre}: {estado}")

    faltan = claves_que_faltan()
    print()
    if faltan:
        piso("[!] Todavia faltan: " + ", ".join(faltan))
        return 1

    piso("Todo listo. Arranca con:  python run.py")
    print()
    return 0


def puerto_ocupado(puerto: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", puerto)) == 0


# ---------------------------------------------------------------------------
# Arranque
# ---------------------------------------------------------------------------


def esperar_backend(timeout: float = 45.0) -> bool:
    """Sondea el backend hasta que responde o se agota el tiempo."""
    limite = time.monotonic() + timeout
    url = f"http://127.0.0.1:{PUERTO_BACKEND}/api/health"
    while time.monotonic() < limite:
        try:
            with urllib.request.urlopen(url, timeout=2) as respuesta:
                if respuesta.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def comandos(python: Path, red: bool) -> tuple[list[str], list[str], str]:
    """Devuelve los comandos del backend y del frontend, y la URL de la app."""
    backend = [
        str(python), "-m", "uvicorn", "backend.main:app",
        "--host", "127.0.0.1", "--port", str(PUERTO_BACKEND),
    ]
    frontend = [
        str(python), "-m", "streamlit", "run", "frontend/app.py",
        "--server.port", str(PUERTO_FRONTEND),
        "--server.headless", "true",
    ]

    if not red:
        return backend, frontend, f"http://localhost:{PUERTO_FRONTEND}"

    cert = RAIZ / "certs" / "server-cert.pem"
    clave = RAIZ / "certs" / "server-key.pem"
    if not (cert.exists() and clave.exists()):
        print("\n[X] Faltan los certificados de certs/. Ver docs/movil.md.\n")
        sys.exit(1)

    frontend += [
        "--server.address", "0.0.0.0",
        "--server.sslCertFile", str(cert),
        "--server.sslKeyFile", str(clave),
    ]
    return backend, frontend, f"https://{ip_local()}:{PUERTO_FRONTEND}"


def ip_local() -> str:
    """IP de esta maquina en la red local (la que tiene que usar el movil)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))  # no envia nada; solo elige la interfaz
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Arranca KekeTranslate.")
    parser.add_argument(
        "--red", action="store_true",
        help="sirve la app por HTTPS a la red local, para grabar desde el movil",
    )
    parser.add_argument(
        "--sin-navegador", action="store_true", help="no abrir el navegador",
    )
    parser.add_argument(
        "--configurar", action="store_true",
        help="pide las claves de API por teclado y las guarda en el .env",
    )
    parser.add_argument(
        "--avanzado", action="store_true",
        help="con --configurar, permite ademas elegir los proveedores",
    )
    args = parser.parse_args()

    if args.configurar:
        return configurar(avanzado=args.avanzado)

    print("\n=== KekeTranslate ===\n")

    python = python_del_entorno()
    piso(f"Python: {python}")
    if not comprobar_dependencias(python):
        return 1

    for puerto, nombre in ((PUERTO_BACKEND, "backend"), (PUERTO_FRONTEND, "frontend")):
        if puerto_ocupado(puerto):
            print(f"\n[X] El puerto {puerto} ya esta en uso ({nombre}).")
            piso("Cierra la instancia anterior y vuelve a intentarlo.\n")
            return 1

    preparar_env()

    for correccion in reparar_env():
        piso(f"[corregido en .env] {correccion}")

    faltan = claves_que_faltan()
    if faltan:
        piso("")
        piso("[!] Faltan claves de API: " + ", ".join(faltan))
        piso("    La app arranca igual, pero los trabajos terminaran en 'failed'.")
        piso(f"    Rellenalas en {RAIZ / '.env'}")

    backend, frontend, url = comandos(python, args.red)

    procesos: list[subprocess.Popen] = []
    try:
        piso("")
        piso("Arrancando el backend...")
        procesos.append(subprocess.Popen(backend, cwd=RAIZ))

        if not esperar_backend():
            print("\n[X] El backend no respondio a tiempo. Revisa el error de arriba.\n")
            return 1
        piso("Backend listo.")

        piso("Arrancando la interfaz...")
        procesos.append(subprocess.Popen(frontend, cwd=RAIZ))
        time.sleep(3)

        print()
        print(f"  Abre la app en:  {url}")
        if args.red:
            piso("(desde el movil, en la misma WiFi; ver docs/movil.md)")
        print()
        piso("Ctrl+C para parar los dos servicios.")
        print()

        if not args.sin_navegador and not args.red:
            webbrowser.open(url)

        while all(p.poll() is None for p in procesos):
            time.sleep(1)

        piso("Uno de los servicios se ha detenido.")
        return 1

    except KeyboardInterrupt:
        print("\n  Parando...")
        return 0

    finally:
        for proceso in procesos:
            if proceso.poll() is None:
                proceso.terminate()
        for proceso in procesos:
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()


if __name__ == "__main__":
    sys.exit(main())
