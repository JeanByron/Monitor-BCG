"""
formatear.py
============
Revisa cómo queda un tomo al COMPONERLO con `app.formato`, sin tocar
nada: los `.jsonl` son la fuente y aquí solo se leen.

Sirve para dos cosas:

- **Ver** una página como quedaría en la aplicación (`--hoja 177`),
  para comprobar contra el libro de papel que el resultado es fiel.
- **Medir** un tomo entero, o todos, y encontrar las páginas que peor
  se componen (`--informe`).

Uso:

    python tools/formatear.py --lista
    python tools/formatear.py "Isócrates" --hoja 177
    python tools/formatear.py "Isócrates" --informe
    python tools/formatear.py --informe            (todos los tomos)
    python tools/formatear.py --sospechosas 20     (las peores páginas)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app import formato                                      # noqa: E402
from app.pdftext import TEXTOS_DIR                           # noqa: E402


def tomos(patron: str = "") -> list[Path]:
    """Los `.jsonl` cuyo nombre contenga `patron`."""
    if not TEXTOS_DIR.exists():
        return []
    clave = formato.normaliza(patron)
    return [
        ruta for ruta in sorted(TEXTOS_DIR.glob("*.jsonl"))
        if not clave or clave in formato.normaliza(ruta.stem)
    ]


def paginas(ruta: Path) -> list[dict]:
    """Las hojas de un tomo (la primera línea es la cabecera)."""
    with ruta.open(encoding="utf-8") as fh:
        fh.readline()
        return [json.loads(l) for l in fh if l.strip()]


def enseñar(ruta: Path, hoja: int) -> None:
    """Una página, tal como quedaría compuesta."""
    for reg in paginas(ruta):
        if reg.get("pdf") != hoja:
            continue
        pagina = formato.componer(reg.get("cuerpo") or "",
                                  reg.get("notas") or "")
        print(f"=== {ruta.stem} · hoja {hoja} "
              f"· página impresa {reg.get('impresa')} ===")
        print(f"    {'VERSO' if pagina.es_verso else 'PROSA'}"
              f" · renglón mediano "
              f"{formato.mediana_de_renglon(reg.get('cuerpo') or '')}")
        if pagina.marcas:
            print(f"\n    al margen: {' · '.join(pagina.marcas)}")
        if pagina.llamadas:
            print(f"    llamadas de nota: {', '.join(pagina.llamadas)}")
        print()
        for tipo, texto in pagina.bloques:
            marca = {"titulo": "»", "verso": "|"}.get(tipo, " ")
            print(f"  {marca} {texto}")
        if pagina.notas:
            print("\n  --- notas ---")
            for numero, texto in pagina.notas:
                print(f"  {numero or '·':>3}  {texto}")
        return
    print(f"El tomo {ruta.stem} no tiene la hoja {hoja}.")


def informe(rutas: list[Path]) -> None:
    """Cifras de cómo se compone cada tomo."""
    print(f"{'tomo':<44} {'hojas':>6} {'rengl.':>7} {'bloques':>8} "
          f"{'r/b':>5} {'versos':>7} {'notas':>6}")
    print("-" * 92)
    total = {"hojas": 0, "renglones_del_pdf": 0, "bloques_compuestos": 0,
             "versos": 0, "llamadas_de_nota": 0}
    for ruta in rutas:
        try:
            datos = formato.revisar(paginas(ruta))
        except (OSError, ValueError) as exc:
            print(f"{ruta.stem[:44]:<44} ilegible: {exc}")
            continue
        for clave in total:
            total[clave] += datos[clave]
        print(f"{ruta.stem[:44]:<44} {datos['hojas']:>6} "
              f"{datos['renglones_del_pdf']:>7} "
              f"{datos['bloques_compuestos']:>8} "
              f"{datos['renglones_por_bloque']:>5} {datos['versos']:>7} "
              f"{datos['llamadas_de_nota']:>6}")
    if len(rutas) > 1:
        ratio = (total["renglones_del_pdf"] / total["bloques_compuestos"]
                 if total["bloques_compuestos"] else 0)
        print("-" * 92)
        print(f"{'TOTAL':<44} {total['hojas']:>6} "
              f"{total['renglones_del_pdf']:>7} "
              f"{total['bloques_compuestos']:>8} {ratio:>5.1f} "
              f"{total['versos']:>7} {total['llamadas_de_nota']:>6}")


def sospechosas(rutas: list[Path], cuantas: int) -> None:
    """
    Las páginas que peor se componen: las que siguen quedando en
    trozos cortos después de componerlas.
    """
    peores = []
    for ruta in rutas:
        try:
            regs = paginas(ruta)
        except (OSError, ValueError):
            continue
        for reg in regs:
            cuerpo = reg.get("cuerpo") or ""
            if reg.get("seccion") != "texto" or len(cuerpo) < 200:
                continue
            pagina = formato.componer(cuerpo, reg.get("notas") or "")
            trozos = [t for tipo, t in pagina.bloques if tipo == "parrafo"]
            if len(trozos) < 3:
                continue
            cortos = sum(1 for t in trozos if len(t) < 90)
            if cortos >= 3:
                peores.append((cortos / len(trozos), cortos, len(trozos),
                               ruta.stem, reg.get("pdf")))
    peores.sort(reverse=True)
    print(f"páginas que quedan en trozos cortos: {len(peores)}")
    for parte, cortos, todos, tomo, hoja in peores[:cuantas]:
        print(f"  {parte:>5.0%}  {cortos:>3}/{todos:<3} {tomo[:46]:<46} "
              f"hoja {hoja}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Compone y revisa el texto de los tomos (no los toca)."
    )
    p.add_argument("tomo", nargs="?", default="",
                   help="parte del nombre del tomo")
    p.add_argument("--hoja", type=int, help="enseña esa hoja compuesta")
    p.add_argument("--informe", action="store_true", help="cifras del tomo")
    p.add_argument("--sospechosas", type=int, nargs="?", const=20,
                   help="las páginas que peor quedan")
    p.add_argument("--lista", action="store_true", help="lista los tomos")
    args = p.parse_args()

    rutas = tomos(args.tomo)
    if not rutas:
        print("No hay textos extraídos que coincidan con eso.")
        return 1
    if args.lista:
        for ruta in rutas:
            print(f"  {ruta.stem}")
        return 0
    if args.hoja is not None:
        enseñar(rutas[0], args.hoja)
        return 0
    if args.sospechosas is not None:
        sospechosas(rutas, args.sospechosas)
        return 0
    informe(rutas if args.informe else rutas[:1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
