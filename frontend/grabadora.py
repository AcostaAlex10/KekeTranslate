"""Grabadora que sube la clase por trozos, mientras se graba.

La anterior guardaba la clase entera en la memoria del navegador y no enviaba
nada hasta pararla: una clase de 4 h eran unos 2,5 GB, y si el telefono se
quedaba sin bateria no quedaba nada. Esta abre la clase en el backend al
empezar y le va anadiendo trozos segun se graban.

## Por que es un componente de altura fija y no widgets de Streamlit

Porque Streamlit repinta el script entero ante cualquier interaccion, y un
`MediaRecorder` no sobrevive a que le recarguen el iframe. Esta comprobado en un
navegador de verdad, y de ahi salen las tres reglas que gobiernan este fichero:

1. **Con el mismo HTML, el iframe sobrevive al repintado.** Se comprobo con tres
   repintados seguidos: el componente seguia vivo, con una sola carga.
2. **Meter o quitar un elemento POR ENCIMA del componente lo recarga.** El
   contador se reinicio de 19 s a 4 s. Por eso lo que hay encima en `app.py`
   vive en huecos (`st.empty()`) que existen siempre: cambiarles el contenido
   **no** lo recarga, tambien comprobado.
3. **Cambiar su HTML lo recarga.** De ahi que aqui no entre ni un dato que
   cambie: ni el estado de la grabacion, ni el trabajo abierto, ni la hora. Todo
   eso vive dentro del propio componente, en JavaScript.

La consecuencia de las tres juntas es que el componente **habla directamente con
el backend**, sin pasar por Python: es el unico sitio de la app donde el
navegador llama a la API, y el motivo de que el backend tenga CORS.
"""

from __future__ import annotations

import json

import streamlit.components.v1 as components

# Cada cuanto suelta `MediaRecorder` un trozo. Treinta segundos es el punto
# medio: mas corto multiplica las peticiones de una clase de cuatro horas —a
# diez segundos serian 1.440—, y mas largo es lo que se pierde si el navegador
# muere sin avisar.
SEGUNDOS_POR_TROZO = 30

# Opus a 32 kbps. Es voz, no musica: se entiende de sobra, y sostiene el motivo
# entero de este componente —una clase de 4 h baja de 2,5 GB a unos 58 MB—.
BITS_POR_SEGUNDO = 32_000

# Alto del iframe. Se fija a mano porque un componente no puede pedir mas sitio
# desde dentro, y hay que dejar hueco para la linea de estado cuando dice algo
# largo —un permiso denegado ocupa tres renglones en pantalla estrecha—.
ALTURA = 175


def grabadora(backend: str, testigo: str, grupos: list[dict], idiomas: list[dict]) -> None:
    """Dibuja la grabadora.

    `grupos` e `idiomas` entran ya resueltos y **no pueden cambiar mientras se
    graba**: forman parte del HTML, y cambiarlo recargaria el componente. En la
    practica no cambian, porque nadie crea un grupo en mitad de una clase.

    El testigo de sesion viaja dentro del HTML. No es una exposicion nueva: ya
    esta en una cookie que este mismo JavaScript puede leer. Es lo que permite
    que el componente llame a la API por su cuenta.
    """
    configuracion = json.dumps(
        {
            "backend": backend.rstrip("/"),
            "testigo": testigo,
            "grupos": grupos,
            "idiomas": idiomas,
            "segundosPorTrozo": SEGUNDOS_POR_TROZO,
            "bitsPorSegundo": BITS_POR_SEGUNDO,
        },
        ensure_ascii=False,
    )
    # Los datos van dentro de un `<script>`, y ahi un `<` no es un caracter
    # cualquiera: una materia llamada `</script>` cerraria el bloque y lo que
    # viniera detras se ejecutaria como codigo. Escapado asi, JSON lo vuelve a
    # convertir en `<` al leerlo y el navegador nunca lo ve como etiqueta.
    configuracion = configuracion.replace("<", "\\u003c")
    components.html(_HTML.replace("__CONFIGURACION__", configuracion), height=ALTURA)


_HTML = """
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font-family: "Source Sans Pro", system-ui, sans-serif;
         color: #E6E8EF; background: transparent; }
  .caja { background: #1B1E29; border: 1px solid #2A2F3E; border-radius: 8px;
          padding: 14px 16px; }
  .fila { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  select { background: #12141C; color: #E6E8EF; border: 1px solid #3A4152;
           border-radius: 6px; padding: 7px 9px; font-size: 14px; flex: 1 1 160px;
           min-width: 0; }
  select:disabled { opacity: .5; }
  button { border: 0; border-radius: 6px; padding: 9px 18px; font-size: 15px;
           font-weight: 600; cursor: pointer; background: #1F6FA8; color: #fff; }
  button.parar { background: #B23A48; }
  button:disabled { opacity: .55; cursor: default; }
  .estado { margin-top: 12px; font-size: 14px; line-height: 1.5; }
  .punto { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
           background: #B23A48; margin-right: 7px; vertical-align: middle; }
  .grabando .punto { animation: latido 1.4s ease-in-out infinite; }
  @keyframes latido { 50% { opacity: .25; } }
  .apagado { color: #9AA3B8; }
  .malo { color: #F08C99; }
  .cifras { font-variant-numeric: tabular-nums; }
</style>

<div class="caja">
  <div class="fila">
    <button id="boton">Empezar a grabar</button>
    <select id="grupo" aria-label="Archivar en"></select>
    <select id="idioma" aria-label="Idioma de los apuntes"></select>
  </div>
  <div class="estado" id="estado"></div>
</div>

<script>
(function () {
  var CFG = __CONFIGURACION__;

  var estado = "parado";      // parado | grabando | cerrando | roto
  var grabador = null, flujo = null, clase = null;
  var siguienteTrozo = 0, subidos = 0, bytes = 0, arranque = 0;
  var cola = Promise.resolve();
  var aviso = "";

  var boton = document.getElementById("boton");
  var caja = document.getElementById("estado");
  var selGrupo = document.getElementById("grupo");
  var selIdioma = document.getElementById("idioma");

  // -- Los selectores, poblados una sola vez ------------------------------
  selGrupo.appendChild(new Option("Sin archivar", ""));
  CFG.grupos.forEach(function (g) {
    selGrupo.appendChild(new Option(g.materia + " · " + g.nombre, g.id));
  });
  selIdioma.appendChild(new Option("Apuntes en el idioma de la clase", ""));
  CFG.idiomas.forEach(function (i) {
    selIdioma.appendChild(new Option("Apuntes en " + i.nombre, i.codigo));
  });

  // -- Utilidades ---------------------------------------------------------
  function pesa(n) {
    return n < 1e6 ? (n / 1e3).toFixed(0) + " kB" : (n / 1e6).toFixed(1) + " MB";
  }

  function reloj(ms) {
    var t = Math.floor(ms / 1000);
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    var dd = function (x) { return (x < 10 ? "0" : "") + x; };
    return (h ? h + ":" : "") + dd(m) + ":" + dd(s);
  }

  function pintar() {
    boton.disabled = (estado === "cerrando");
    selGrupo.disabled = selIdioma.disabled = (estado !== "parado");
    boton.className = estado === "grabando" ? "parar" : "";

    if (estado === "parado") {
      boton.textContent = "Empezar a grabar";
      caja.className = "estado apagado";
      caja.innerHTML = aviso ||
        "Deja el teléfono cerca de quien habla. El audio se va enviando " +
        "mientras grabas, así que puedes cerrar esto sin perder la clase.";
      return;
    }
    if (estado === "roto") {
      boton.textContent = "Empezar a grabar";
      caja.className = "estado malo";
      caja.innerHTML = aviso;
      return;
    }
    if (estado === "cerrando") {
      boton.textContent = "Cerrando…";
      caja.className = "estado apagado";
      caja.innerHTML = "Enviando lo que queda…";
      return;
    }

    var pendientes = siguienteTrozo - subidos;
    caja.className = "estado grabando";
    boton.textContent = "Parar y procesar";
    caja.innerHTML =
      '<span class="punto"></span><span class="cifras">' +
      reloj(Date.now() - arranque) + "</span> · " + pesa(bytes) + " enviados" +
      (pendientes > 0 ? " · " + pendientes + " sin enviar" : "") +
      (aviso ? '<br><span class="malo">' + aviso + "</span>" : "");
  }

  function pedir(ruta, opciones) {
    opciones = opciones || {};
    opciones.headers = Object.assign(
      { Authorization: "Bearer " + CFG.testigo }, opciones.headers || {}
    );
    return fetch(CFG.backend + ruta, opciones).then(function (r) {
      if (r.ok) { return r.json(); }
      return r.json().catch(function () { return {}; }).then(function (cuerpo) {
        var error = new Error(cuerpo.detail || ("Error " + r.status));
        error.codigo = r.status;
        throw error;
      });
    });
  }

  function esperar(ms) {
    return new Promise(function (listo) { setTimeout(listo, ms); });
  }

  // -- Subida por turnos --------------------------------------------------
  // Los trozos van en cadena y no en paralelo: el fichero valido es la
  // concatenacion **en orden**, y dos peticiones a la vez podrian llegar
  // cambiadas. Reintentar el mismo trozo siempre es seguro: si el servidor ya
  // lo tenia, lo reconoce como repetido y lo ignora.
  function encolar(numero, trozo) {
    cola = cola.then(function () { return subir(numero, trozo); });
  }

  function subir(numero, trozo, intento) {
    intento = intento || 0;
    return pedir("/api/jobs/" + clase + "/parte?n=" + numero, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: trozo,
    }).then(function (job) {
      subidos = numero + 1;
      bytes = job.file_size_bytes || bytes;
      aviso = "";
      pintar();
    }).catch(function (error) {
      // Un 413 o un 409 no se arreglan reintentando: el servidor ha dicho que
      // no, y con motivo. Lo demas casi siempre es la red yendose un momento.
      if (error.codigo === 413 || error.codigo === 409 || intento >= 8) {
        aviso = error.message;
        pintar();
        return;
      }
      aviso = "Sin conexión con el servidor; reintentando…";
      pintar();
      return esperar(Math.min(30000, 1000 * Math.pow(2, intento))).then(
        function () { return subir(numero, trozo, intento + 1); }
      );
    });
  }

  // -- Empezar y parar ----------------------------------------------------
  function tipoQueSabeGrabar() {
    var candidatos = [
      "audio/webm;codecs=opus", "audio/webm",
      "audio/ogg;codecs=opus", "audio/mp4",
    ];
    for (var i = 0; i < candidatos.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidatos[i])) {
        return candidatos[i];
      }
    }
    return null;
  }

  function empezar() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      estado = "roto";
      aviso = "Este navegador no da acceso al micrófono. Si entras desde el " +
              "teléfono, tiene que ser por https.";
      return pintar();
    }
    var tipo = tipoQueSabeGrabar();
    if (!tipo) {
      estado = "roto";
      aviso = "Este navegador no sabe grabar audio. Graba con la aplicación " +
              "del teléfono y sube el fichero.";
      return pintar();
    }

    aviso = "";
    navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: true },
    }).then(function (entrada) {
      flujo = entrada;
      var nombre = "clase " + new Date().toLocaleString();
      var ruta = "/api/jobs/grabacion?tipo=" + encodeURIComponent(tipo) +
                 "&nombre=" + encodeURIComponent(nombre);
      if (selGrupo.value) { ruta += "&grupo_id=" + selGrupo.value; }
      if (selIdioma.value) { ruta += "&idioma=" + selIdioma.value; }
      return pedir(ruta, { method: "POST" });
    }).then(function (job) {
      clase = job.id;
      siguienteTrozo = subidos = bytes = 0;
      arranque = Date.now();
      grabador = new MediaRecorder(flujo, {
        mimeType: tipo, audioBitsPerSecond: CFG.bitsPorSegundo,
      });
      grabador.ondataavailable = function (evento) {
        if (evento.data && evento.data.size) {
          encolar(siguienteTrozo++, evento.data);
        }
      };
      grabador.start(CFG.segundosPorTrozo * 1000);
      estado = "grabando";
      pintar();
    }).catch(function (error) {
      soltarMicrofono();
      estado = "roto";
      aviso = error && error.name === "NotAllowedError"
        ? "No diste permiso al micrófono. Actívalo en el candado de la barra " +
          "de direcciones y vuelve a intentarlo."
        : (error.message || "No se pudo empezar a grabar.");
      pintar();
    });
  }

  function soltarMicrofono() {
    if (flujo) { flujo.getTracks().forEach(function (p) { p.stop(); }); }
    flujo = null;
  }

  function parar() {
    estado = "cerrando";
    pintar();
    if (grabador && grabador.state !== "inactive") { grabador.stop(); }
    soltarMicrofono();
    // Se espera a que la cola termine: el ultimo trozo lo suelta `stop()` y
    // cerrar antes dejaria fuera el final de la clase.
    cola.then(function () {
      return pedir("/api/jobs/" + clase + "/cerrar", { method: "POST" });
    }).then(function () {
      estado = "parado";
      aviso = "Clase enviada. La tienes en <b>Mis clases</b>, procesándose.";
      pintar();
    }).catch(function (error) {
      estado = "roto";
      aviso = (error.message || "No se pudo cerrar la grabación.") +
              " Lo grabado no se ha perdido: está en <b>Mis clases</b>.";
      pintar();
    });
  }

  boton.addEventListener("click", function () {
    if (estado === "grabando") { parar(); } else { empezar(); }
  });

  window.addEventListener("beforeunload", function (evento) {
    if (estado === "grabando") { evento.preventDefault(); evento.returnValue = ""; }
  });

  setInterval(function () { if (estado === "grabando") { pintar(); } }, 1000);
  pintar();
})();
</script>
"""
