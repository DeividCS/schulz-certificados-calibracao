"""
Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A.
Fase 04 - Gera os arquivos de teste usados no plano de testes (dados/testes/).

Todos partem da saída real do Calypso (dados/04__Registro_MMC.xlsx). Só os valores
indicados abaixo são alterados; o resto do arquivo fica igual ao original.

  Registro_MMC_aprovado_00092.xlsx  -> posições dentro da tolerância (caso APROVADO)
  Registro_MMC_padrao_vencido.xlsx  -> data da medição 15/09/2026 (padrão 500.011 vence 31/08/2026)
  Registro_MMC_sem_report.xlsx      -> aba "Report" renomeada (arquivo fora do formato)
  Registro_MMC_campo_vazio.xlsx     -> um "Actual" apagado (TR-07: não vira zero, gera anomalia)

Uso:  python gerar_dados_teste.py
"""

from __future__ import annotations

import datetime as dt
import warnings
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

BASE = Path(__file__).resolve().parent
ORIGEM = BASE / "dados" / "04__Registro_MMC.xlsx"
DESTINO = BASE / "dados" / "testes"

# posição verdadeira bem abaixo de 0,100 mm (valores das duas medições)
POSICOES_OK = {
    "62P_POSIÇÃO 1": (0.0412, 0.0418), "62P_POSIÇÃO 2": (0.0365, 0.0371),
    "13P_POSIÇÃO 1": (0.0523, 0.0519), "13P_POSIÇÃO 2": (0.0287, 0.0291),
    "106P_POSIÇÃO 1": (0.0198, 0.0202), "106P_POSIÇÃO 2": (0.0634, 0.0630),
}


def _linhas_resultado(ws):
    """Devolve (linha, parametro) de cada resultado, na ordem em que aparecem."""
    inicio = next(c.row for linha in ws.iter_rows() for c in linha if c.value == "Characteristic")
    for r in range(inicio + 1, ws.max_row + 1):
        valor = ws.cell(row=r, column=1).value
        if valor:
            yield r, str(valor).strip()


def aprovado() -> Path:
    wb = openpyxl.load_workbook(ORIGEM)
    ws = wb["Report"]
    vistos: dict[str, int] = {}
    for r, parametro in _linhas_resultado(ws):
        if parametro in POSICOES_OK:
            i = vistos.get(parametro, 0)
            vistos[parametro] = i + 1
            actual = POSICOES_OK[parametro][min(i, 1)]
            nominal = ws.cell(row=r, column=3).value or 0
            ws.cell(row=r, column=2).value = actual
            ws.cell(row=r, column=6).value = round(actual - nominal, 7)
    ws["B5"] = "703.1208_TESTE_APROVADO"
    ws["D5"] = dt.datetime(2026, 8, 20)
    destino = DESTINO / "Registro_MMC_aprovado_00092.xlsx"
    wb.save(destino)
    return destino


def padrao_vencido() -> Path:
    wb = openpyxl.load_workbook(ORIGEM)
    wb["Report"]["D5"] = dt.datetime(2026, 9, 15)
    wb["Report"]["B5"] = "703.1208_TESTE_VENCIDO"
    destino = DESTINO / "Registro_MMC_padrao_vencido.xlsx"
    wb.save(destino)
    return destino


def sem_report() -> Path:
    wb = openpyxl.load_workbook(ORIGEM)
    wb["Report"].title = "Planilha1"
    destino = DESTINO / "Registro_MMC_sem_report.xlsx"
    wb.save(destino)
    return destino


def campo_vazio() -> Path:
    wb = openpyxl.load_workbook(ORIGEM)
    ws = wb["Report"]
    linhas = list(_linhas_resultado(ws))
    r, _ = linhas[-1]                      # último parâmetro do segundo bloco
    ws.cell(row=r, column=2).value = None
    ws["B5"] = "703.1208_TESTE_VAZIO"
    ws["D5"] = dt.datetime(2026, 8, 21)
    destino = DESTINO / "Registro_MMC_campo_vazio.xlsx"
    wb.save(destino)
    return destino


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for funcao in (aprovado, padrao_vencido, sem_report, campo_vazio):
        print("gerado:", funcao().relative_to(BASE))


if __name__ == "__main__":
    main()
