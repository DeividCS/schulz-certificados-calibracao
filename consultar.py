"""
Executa as consultas do arquivo 04_consultas.sql sem precisar instalar o sqlite3.

    python consultar.py                     # tabela de resultados do certificado 00091/2026
    python consultar.py 00091/2026
    python consultar.py --consulta padroes  # padrões vencidos ou sem validade
    python consultar.py --consulta tabelas  # lista as tabelas do banco
    python consultar.py --consulta historico --codigo 703.1208
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# Evita erro de acentuação em consoles antigos do Windows
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(errors="replace")


def carregar_consultas() -> dict[str, str]:
    """Separa o 04_consultas.sql pelos marcadores '-- [nome]'."""
    texto = (BASE / "04_consultas.sql").read_text(encoding="utf-8")
    partes = re.split(r"^-- \[(\w+)\].*$", texto, flags=re.MULTILINE)
    return {partes[i]: partes[i + 1].strip().rstrip(";") for i in range(1, len(partes), 2)}


def executar(nome: str, parametros: dict, banco: Path = BASE / "schulz_calibracao.db"):
    con = sqlite3.connect(banco)
    try:
        cur = con.execute(carregar_consultas()[nome], parametros)
        return [d[0] for d in cur.description], cur.fetchall()
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("certificado", nargs="?", default="00091/2026")
    p.add_argument("--consulta", default="certificado", choices=["certificado", "historico", "padroes", "tabelas"])
    p.add_argument("--codigo", default="703.1208")
    a = p.parse_args()
    colunas, linhas = executar(a.consulta, {"certificado": a.certificado, "codigo": a.codigo})
    if a.consulta == "certificado":
        colunas_curtas = ["sequencia", "parametro", "valor_nominal", "tol_mais", "tol_menos", "valor_obtido", "erro", "status"]
        idx = [colunas.index(c) for c in colunas_curtas]
        if linhas:
            l0 = linhas[0]
            print(f"Certificado {l0[0]} | emissão {l0[1]} | {l0[3]} {l0[4]} | solicitante {l0[2]}")
            print(f"Padrão {l0[6]} (cert. {l0[7]}, válido até {l0[8]}) | U = {l0[16]} mm, k = {l0[17]}")
        colunas, linhas = colunas_curtas, [[l[i] for i in idx] for l in linhas]
    larg = [max(len(str(c)), *(len(str(l[i])) for l in linhas)) if linhas else len(c) for i, c in enumerate(colunas)]
    print("  ".join(str(c).ljust(w) for c, w in zip(colunas, larg)))
    for l in linhas:
        print("  ".join(str(v).ljust(w) for v, w in zip(l, larg)))


if __name__ == "__main__":
    main()
