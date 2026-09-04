"""Genera PDFs minimos pero validos para los tests.

Se construye el fichero a mano, con su tabla de referencias cruzadas, en vez de
usar una libreria de maquetacion: asi los tests de extraccion se ejecutan sobre
un PDF de verdad sin anadir una dependencia solo para eso.
"""

from __future__ import annotations


def _objeto(numero: int, cuerpo: str) -> str:
    return f"{numero} 0 obj\n{cuerpo}\nendobj\n"


def pdf_con_texto(paginas: list[list[str]]) -> bytes:
    """Devuelve un PDF con una pagina por lista de lineas.

    Cada linea se dibuja con la fuente Helvetica, que es una de las catorce
    estandar y no hay que incrustar.
    """
    objetos: list[str] = []
    total_paginas = len(paginas)

    # 1: catalogo, 2: arbol de paginas, luego por cada pagina su objeto y su
    # contenido, y al final la fuente.
    primer_id_pagina = 3
    id_fuente = primer_id_pagina + total_paginas * 2

    ids_pagina = [primer_id_pagina + i * 2 for i in range(total_paginas)]
    kids = " ".join(f"{i} 0 R" for i in ids_pagina)

    objetos.append(_objeto(1, "<< /Type /Catalog /Pages 2 0 R >>"))
    objetos.append(
        _objeto(2, f"<< /Type /Pages /Kids [{kids}] /Count {total_paginas} >>")
    )

    for indice, lineas in enumerate(paginas):
        id_pagina = ids_pagina[indice]
        id_contenido = id_pagina + 1

        instrucciones = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for linea in lineas:
            escapada = (
                linea.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            )
            instrucciones.append(f"({escapada}) Tj")
            instrucciones.append("T*")
        instrucciones.append("ET")
        flujo = "\n".join(instrucciones)

        objetos.append(
            _objeto(
                id_pagina,
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {id_contenido} 0 R "
                f"/Resources << /Font << /F1 {id_fuente} 0 R >> >> >>",
            )
        )
        objetos.append(
            _objeto(
                id_contenido,
                f"<< /Length {len(flujo)} >>\nstream\n{flujo}\nendstream",
            )
        )

    objetos.append(
        _objeto(id_fuente, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    )

    # Montaje final, calculando el desplazamiento de cada objeto para la xref.
    cabecera = "%PDF-1.4\n"
    salida = cabecera
    desplazamientos: list[int] = []
    for objeto in objetos:
        desplazamientos.append(len(salida.encode("latin-1")))
        salida += objeto

    inicio_xref = len(salida.encode("latin-1"))
    total_objetos = len(objetos) + 1

    salida += f"xref\n0 {total_objetos}\n0000000000 65535 f \n"
    for desplazamiento in desplazamientos:
        salida += f"{desplazamiento:010d} 00000 n \n"
    salida += (
        f"trailer\n<< /Size {total_objetos} /Root 1 0 R >>\n"
        f"startxref\n{inicio_xref}\n%%EOF\n"
    )

    return salida.encode("latin-1")


def pdf_escaneado() -> bytes:
    """Un PDF de una pagina sin nada de texto, como un escaneo."""
    return pdf_con_texto([[]])
