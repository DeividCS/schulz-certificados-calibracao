"""
Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A.
Fase 02 - Extração da saída da MMC (Calypso) e carga no banco SQLite.

Fluxo (regras da Fase 01 entre parênteses):
  1. cria o banco a partir do 03_banco.sql, somente se ele ainda não existir;
  2. carrega cadastros: aba "BANCO DE DADOS" (técnicos, padrões, áreas) e dados/padroes.csv;
  3. lê a aba "Report" do Calypso localizando campos pelo NOME do cabeçalho (EXT-03);
  4. junta os dois blocos de medição (medida 1 e medida 2) por parâmetro;
  5. lê o arquivo de complemento (dados que não existem no Calypso) e saneia (TR-01 a TR-13);
  6. calcula média, desvio padrão, erro e status;
  7. grava tudo em uma única transação (LD-01) e salva as anomalias em logs/ (RF-18).

Uso (terminal do VS Code; no Windows use "py" se "python" não funcionar):
    pip install -r requirements.txt
    python extrator.py                       # importa a amostra do certificado 00091/2026
    python extrator.py --somente-estrutura   # cria apenas o banco vazio (3 tabelas)
    python extrator.py --recriar             # apaga o banco e importa novamente
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sqlite3
import statistics
import sys
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

BASE = Path(__file__).resolve().parent

# Evita erro de acentuação em consoles antigos do Windows
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(errors="replace")
TOLERANCIA_CONSISTENCIA = 1e-6  # diferença máxima aceita entre Deviation e Actual - Nominal
VALORES_NULOS = {"", "-", "--", "---", "------", "N/A", "n/a", "00/00/0000",
                 "Não consta", "Não se aplica", "Não informado"}


class ErroImportacao(Exception):
    """Erro de regra de negócio que impede a importação."""


# ----------------------------------------------------------------------
# Funções de higienização (regras TR da Fase 01)
# ----------------------------------------------------------------------
def limpar_texto(valor) -> str | None:
    """TR-01/TR-02/TR-06: remove espaços nas pontas e repetidos; marcadores de vazio viram None."""
    if valor is None:
        return None
    texto = " ".join(str(valor).split())
    return None if texto in VALORES_NULOS else texto


def chave_comparacao(texto: str | None) -> str:
    """TR-05: versão sem acento e minúscula, usada só para comparar nomes."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def codigo_padrao(valor) -> str | None:
    """TR-11: 500008 (número no Calypso) -> '500.008' (formato do certificado)."""
    texto = limpar_texto(valor)
    if texto is None:
        return None
    digitos = texto.replace(".", "")
    if digitos.isdigit() and len(digitos) == 6:
        return f"{digitos[:3]}.{digitos[3:]}"
    return texto


def para_real(valor) -> float | None:
    """TR-12: aceita número, '0,001' e '20,4°'; texto com vírgula e ponto é ambíguo e é rejeitado."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(" ", "").replace("°C", "").replace("°", "")
    if texto in VALORES_NULOS:
        return None
    if "," in texto and "." in texto:
        raise ErroImportacao(f"TR-12: número ambíguo (vírgula e ponto): {valor!r}")
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        raise ErroImportacao(f"TR-12: valor não numérico: {valor!r}") from None


def para_data_iso(valor) -> str | None:
    """TR-09: datetime do Excel, 'DD/MM/AAAA' ou 'AAAA-MM-DD' -> 'AAAA-MM-DD'."""
    if isinstance(valor, dt.datetime):
        return valor.date().isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    texto = limpar_texto(valor)
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            pass
    raise ErroImportacao(f"TR-09: data inválida: {valor!r}")


def para_hora(valor) -> str | None:
    if isinstance(valor, (dt.time, dt.datetime)):
        return valor.strftime("%H:%M:%S")
    texto = limpar_texto(valor)
    return texto[:8] if texto else None


def meses_entre(inicio: str, fim: str) -> int | None:
    a, b = dt.date.fromisoformat(inicio), dt.date.fromisoformat(fim)
    meses = (b.year - a.year) * 12 + (b.month - a.month)
    return meses if meses > 0 else None


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


# ----------------------------------------------------------------------
# Leitura da aba Report do Calypso
# ----------------------------------------------------------------------
@dataclass
class Caracteristica:
    parametro: str
    nominal: float
    tol_sup: float
    tol_inf: float
    medidas: list[float] = field(default_factory=list)


@dataclass
class RelatorioCalypso:
    cabecalho: dict
    caracteristicas: list[Caracteristica]
    anomalias: list[tuple[str, str]]


ROTULOS = {
    "Measurement Plan": "plano_medicao", "Date": "data", "Order": "ordem",
    "Drawing No.": "desenho", "Time": "hora", "Part No.": "numero_peca",
    "Cliente": "cliente", "Metrologista": "metrologista", "MMC": "mmc", "Fornecedor": "fornecedor",
}
COLUNAS_RESULTADO = ["Characteristic", "Actual", "Nominal", "Upper Tol", "Lower Tol", "Deviation"]


def ler_report(caminho: Path) -> RelatorioCalypso:
    wb = openpyxl.load_workbook(caminho, data_only=True)
    if "Report" not in wb.sheetnames:
        raise ErroImportacao("EXT-03: aba obrigatória 'Report' não encontrada")
    ws = wb["Report"]
    anomalias: list[tuple[str, str]] = []

    # Cabeçalho: o valor fica na célula logo abaixo do rótulo
    cabecalho, linha_resultados, coluna_inicial = {}, None, None
    for linha in ws.iter_rows():
        for celula in linha:
            texto = limpar_texto(celula.value)
            if texto in ROTULOS:
                cabecalho[ROTULOS[texto]] = ws.cell(row=celula.row + 1, column=celula.column).value
            if texto == "Characteristic":
                linha_resultados, coluna_inicial = celula.row, celula.column
    if linha_resultados is None:
        raise ErroImportacao("EXT-03: cabeçalho 'Characteristic' não encontrado na aba Report")

    encontrados = [limpar_texto(ws.cell(row=linha_resultados, column=coluna_inicial + i).value)
                   for i in range(len(COLUNAS_RESULTADO))]
    if encontrados != COLUNAS_RESULTADO:
        raise ErroImportacao(f"RF-02: colunas inesperadas no Report: {encontrados}")

    # Resultados: blocos separados por linha em branco (bloco 1 = medida 1, bloco 2 = medida 2)
    por_parametro: dict[str, Caracteristica] = {}
    ordem: list[str] = []
    bloco, anterior_vazia = 1, False
    for r in range(linha_resultados + 1, ws.max_row + 1):
        valores = [ws.cell(row=r, column=coluna_inicial + i).value for i in range(6)]
        parametro = limpar_texto(valores[0])
        if parametro is None:
            if not anterior_vazia and por_parametro:
                bloco += 1
            anterior_vazia = True
            continue
        anterior_vazia = False
        actual, nominal, tol_sup, tol_inf, desvio = (para_real(v) for v in valores[1:])
        if None in (actual, nominal, tol_sup, tol_inf):
            anomalias.append(("DQ-COMPLETUDE", f"Linha {r}: '{parametro}' com campo numérico vazio "
                                               f"(TR-07: não substituído por zero; linha ignorada)"))
            continue
        if parametro not in por_parametro:
            por_parametro[parametro] = Caracteristica(parametro, nominal, tol_sup, tol_inf)
            ordem.append(parametro)
        carac = por_parametro[parametro]
        if (carac.nominal, carac.tol_sup, carac.tol_inf) != (nominal, tol_sup, tol_inf):
            anomalias.append(("DQ-CONSISTENCIA", f"Linha {r}: '{parametro}' com nominal ou tolerância "
                                                 f"diferente entre as medições"))
        carac.medidas.append(actual)
        if desvio is not None and abs(desvio - (actual - nominal)) > TOLERANCIA_CONSISTENCIA:
            anomalias.append(("DQ-CONSISTENCIA",
                              f"Linha {r} (medida {bloco}): '{parametro}' Deviation={desvio:.7f} "
                              f"diferente de Actual-Nominal={actual - nominal:.7f}"))

    if not por_parametro:  # RN-01: arquivo sem resultados não gera certificado
        raise ErroImportacao("RN-01: a aba Report não contém resultados de medição; importação cancelada")
    return RelatorioCalypso(cabecalho, [por_parametro[p] for p in ordem], anomalias)


def ler_banco_de_dados(caminho: Path) -> dict:
    """Aba BANCO DE DADOS: B = metrologistas, C = padrões MMC, D = fornecedores, E = áreas."""
    wb = openpyxl.load_workbook(caminho, data_only=True)
    if "BANCO DE DADOS" not in wb.sheetnames:
        raise ErroImportacao("EXT-03: aba 'BANCO DE DADOS' não encontrada no arquivo de cadastros")
    ws = wb["BANCO DE DADOS"]
    saida = {}
    for nome, col in {"metrologistas": 2, "padroes": 3, "fornecedores": 4, "areas": 5}.items():
        itens = []
        for r in range(1, ws.max_row + 1):
            v = ws.cell(row=r, column=col).value
            v = codigo_padrao(v) if nome == "padroes" else limpar_texto(v)
            if v and v not in itens:
                itens.append(v)
        saida[nome] = itens
    return saida


def ler_padroes_csv(caminho: Path) -> list[dict]:
    with open(caminho, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


# ----------------------------------------------------------------------
# Regras de cálculo (reproduzem a aba Registro, com o sinal corrigido)
# ----------------------------------------------------------------------
def calcular(carac: Caracteristica, incerteza: float) -> dict:
    media = statistics.fmean(carac.medidas)
    desvio_padrao = statistics.stdev(carac.medidas) if len(carac.medidas) > 1 else None
    erro = media - carac.nominal
    limite_superior = carac.nominal + carac.tol_sup
    limite_inferior = carac.nominal + carac.tol_inf  # tol_inf já é negativa ou zero
    # Faixa de guarda da planilha: reprova se (média ± U) ultrapassar o limite.
    # A planilha usa "C - E" no limite inferior, o que inverte o sinal (ver relatório, A-03).
    reprovado = (media + incerteza) > limite_superior or (media - incerteza) < limite_inferior
    return {
        "medida_1": carac.medidas[0],
        "medida_2": carac.medidas[1] if len(carac.medidas) > 1 else None,
        "valor_obtido": media,
        "erro": erro,
        "desvio_padrao": desvio_padrao,
        "status": "REPROVADO" if reprovado else "APROVADO",
    }


# ----------------------------------------------------------------------
# Banco
# ----------------------------------------------------------------------
def abrir_banco(caminho: Path, recriar: bool) -> sqlite3.Connection:
    if recriar and caminho.exists():
        caminho.unlink()
    novo = not caminho.exists()
    con = sqlite3.connect(caminho)
    if novo:
        con.executescript((BASE / "03_banco.sql").read_text(encoding="utf-8"))
    con.execute("PRAGMA foreign_keys = ON")
    return con


def obter_ou_criar_pessoa(con, nome, tipo, nome_abreviado=None, matricula=None, setor=None) -> int:
    for id_, n, abrev in con.execute(
        "SELECT id_pessoa, nome, nome_abreviado FROM tecnico_solicitante WHERE tipo = ?", (tipo,)
    ):
        if chave_comparacao(nome) in (chave_comparacao(n), chave_comparacao(abrev)):
            return id_
    cur = con.execute(
        "INSERT INTO tecnico_solicitante (nome, nome_abreviado, matricula, tipo, setor) VALUES (?,?,?,?,?)",
        (nome, nome_abreviado, matricula, tipo, setor),
    )
    return cur.lastrowid


def obter_ou_criar_equipamento(con, codigo, denominacao, categoria, **extras) -> int:
    linha = con.execute("SELECT id_equipamento FROM equipamento WHERE codigo = ?", (codigo,)).fetchone()
    if linha:
        if extras:  # completa campos vazios sem apagar o que já existe
            sets = ", ".join(f"{k} = COALESCE(?, {k})" for k in extras)
            con.execute(f"UPDATE equipamento SET {sets} WHERE codigo = ?", (*extras.values(), codigo))
        return linha[0]
    colunas = ["codigo", "denominacao", "categoria", *extras]
    cur = con.execute(
        f"INSERT INTO equipamento ({','.join(colunas)}) VALUES ({','.join('?' * len(colunas))})",
        (codigo, denominacao, categoria, *extras.values()),
    )
    return cur.lastrowid


def salvar_anomalias(anomalias, certificado, arquivo, hash_arquivo) -> Path | None:
    if not anomalias:
        return None
    pasta = BASE / "logs"
    pasta.mkdir(exist_ok=True)
    agora = dt.datetime.now()
    destino = pasta / f"anomalias_{certificado.replace('/', '-')}_{agora:%Y%m%d_%H%M%S}.csv"
    with open(destino, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["data_hora", "certificado", "arquivo", "sha256", "tipo", "descricao"])
        for tipo, descricao in anomalias:
            w.writerow([f"{agora:%Y-%m-%d %H:%M:%S}", certificado, arquivo, hash_arquivo, tipo, descricao])
    return destino


# ----------------------------------------------------------------------
# Importação
# ----------------------------------------------------------------------
def importar(args) -> int:
    registro, banco = Path(args.registro), Path(args.banco)
    comp = json.loads(Path(args.complemento).read_text(encoding="utf-8"))
    relatorio = ler_report(registro)
    cadastros = ler_banco_de_dados(Path(args.cadastros))
    padroes = ler_padroes_csv(Path(args.padroes))
    cab, anomalias = relatorio.cabecalho, list(relatorio.anomalias)

    # Saneamento do complemento
    certificado = limpar_texto(comp["numero_certificado"])
    data_emissao = para_data_iso(comp.get("data_emissao"))
    codigo_item = limpar_texto(comp.get("codigo_item")) or (limpar_texto(cab.get("plano_medicao")) or "").split("_")[0]
    padrao = codigo_padrao(comp["padrao"])
    temperatura = para_real(comp.get("temperatura"))
    incerteza_mm = round(float(comp["incerteza_um"]) / 1000, 3)  # mesma regra da planilha: ARRED(G58/1000;3)
    fator_k = para_real(comp.get("fator_k"))
    data_medicao = para_data_iso(cab.get("data"))
    if not certificado or not data_medicao or not padrao:
        raise ErroImportacao("RN-01: número do certificado, data da medição e padrão são obrigatórios")

    # Divergências entre o Calypso e o certificado (pendências de validação)
    mmc_calypso = codigo_padrao(cab.get("mmc"))
    if mmc_calypso and mmc_calypso != padrao:
        anomalias.append(("DIVERGENCIA", f"MMC no Calypso = {mmc_calypso}, mas o certificado cita o padrão "
                                         f"{padrao} (usado o do certificado)"))
    metrologista = limpar_texto(cab.get("metrologista"))
    if chave_comparacao(metrologista) != chave_comparacao(comp["tecnico"]):
        anomalias.append(("PADRONIZACAO", f"Metrologista no Calypso = '{metrologista}' x certificado = "
                                          f"'{comp['tecnico']}' (relacionado pelo nome abreviado)"))
    if chave_comparacao(comp["solicitante"]) not in {chave_comparacao(a) for a in cadastros["areas"]}:
        anomalias.append(("CADASTRO", f"Solicitante '{comp['solicitante']}' não existe na lista de áreas do "
                                      f"BANCO DE DADOS (cadastrado como nova área; confirmar com a Schulz)"))

    hash_arquivo = sha256(registro)
    con = abrir_banco(banco, args.recriar)
    try:
        ja = con.execute("SELECT COUNT(*) FROM medicao WHERE numero_certificado = ?", (certificado,)).fetchone()[0]
        if ja:  # RN-10 / RN-11: não sobrescreve nem duplica o que já foi importado
            raise ErroImportacao(f"RN-10: o certificado {certificado} já foi importado ({ja} parâmetros). "
                                 f"Nada foi alterado. Use --recriar apenas em ambiente de teste.")
        with con:  # transação: grava tudo ou nada (LD-01 / RN-29)
            for nome in cadastros["metrologistas"]:
                obter_ou_criar_pessoa(con, nome, "TECNICO", nome_abreviado=nome, setor="Metrologia")
            for area in cadastros["areas"]:
                obter_ou_criar_pessoa(con, area, "SOLICITANTE", setor=area)
            for codigo in cadastros["padroes"]:
                obter_ou_criar_equipamento(con, codigo, "Máquina de medição por coordenadas", "PADRAO")
            for p in padroes:
                obter_ou_criar_equipamento(
                    con, codigo_padrao(p["codigo"]), limpar_texto(p["denominacao"]), "PADRAO",
                    fabricante=limpar_texto(p["fabricante"]), modelo=limpar_texto(p["modelo"]),
                    numero_serie=limpar_texto(p["numero_serie"]), unidade=limpar_texto(p["unidade"]) or "mm",
                    resolucao=para_real(p["resolucao"]),
                    certificado_calibracao=limpar_texto(p["certificado_calibracao"]),
                    data_ultima_calibracao=para_data_iso(p["data_ultima_calibracao"]),
                    data_proxima_calibracao=para_data_iso(p["data_proxima_calibracao"]),
                )

            id_tecnico = obter_ou_criar_pessoa(con, metrologista or comp["tecnico"], "TECNICO",
                                               nome_abreviado=metrologista)
            con.execute("UPDATE tecnico_solicitante SET nome = ?, matricula = ? WHERE id_pessoa = ?",
                        (limpar_texto(comp["tecnico"]), limpar_texto(comp.get("matricula")), id_tecnico))
            id_signatario = obter_ou_criar_pessoa(con, limpar_texto(comp["signatario"]), "SIGNATARIO",
                                                  setor="Metrologia")
            id_solicitante = obter_ou_criar_pessoa(con, limpar_texto(comp["solicitante"]), "SOLICITANTE",
                                                   setor=limpar_texto(comp["solicitante"]))

            id_padrao = con.execute("SELECT id_equipamento FROM equipamento WHERE codigo = ?", (padrao,)).fetchone()
            if not id_padrao:
                raise ErroImportacao(f"RN-05: padrão {padrao} não cadastrado em dados/padroes.csv")
            proxima_item = para_data_iso(comp.get("proxima_calibracao_item"))
            id_item = obter_ou_criar_equipamento(
                con, codigo_item, limpar_texto(comp["denominacao"]), "ITEM_CALIBRADO",
                fabricante=limpar_texto(comp.get("fabricante")), modelo=limpar_texto(comp.get("modelo")),
                numero_serie=limpar_texto(comp.get("numero_serie")), unidade=limpar_texto(comp.get("unidade")) or "mm",
                resolucao=para_real(comp.get("resolucao")),
                data_ultima_calibracao=data_emissao, data_proxima_calibracao=proxima_item,
                periodicidade_meses=meses_entre(data_emissao, proxima_item) if data_emissao and proxima_item else None,
                id_solicitante=id_solicitante,
            )

            for seq, carac in enumerate(relatorio.caracteristicas, start=1):
                calc = calcular(carac, incerteza_mm)
                con.execute(
                    """INSERT INTO medicao (
                        numero_certificado, id_equipamento, id_padrao, id_tecnico, id_solicitante,
                        id_signatario, plano_medicao, fornecedor, data_medicao, hora_medicao, data_emissao,
                        sequencia, parametro, valor_nominal, tolerancia_superior, tolerancia_inferior,
                        medida_1, medida_2, valor_obtido, erro, desvio_padrao,
                        incerteza_expandida, fator_k, temperatura_ambiente, unidade, status,
                        arquivo_origem, hash_arquivo)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (certificado, id_item, id_padrao[0], id_tecnico, id_solicitante, id_signatario,
                     limpar_texto(cab.get("plano_medicao")), limpar_texto(cab.get("fornecedor")),
                     data_medicao, para_hora(cab.get("hora")), data_emissao,
                     seq, carac.parametro, carac.nominal, carac.tol_sup, carac.tol_inf,
                     calc["medida_1"], calc["medida_2"], calc["valor_obtido"], calc["erro"],
                     calc["desvio_padrao"], incerteza_mm, fator_k, temperatura, "mm",
                     calc["status"], registro.name, hash_arquivo),
                )
    finally:
        con.close()

    log = salvar_anomalias(anomalias, certificado, registro.name, hash_arquivo)
    print(f"Banco: {banco.name}")
    print(f"Arquivo importado: {registro.name}  (SHA-256 {hash_arquivo[:16]}...)")
    print(f"Certificado {certificado}: {len(relatorio.caracteristicas)} parâmetros gravados")
    print(f"Anomalias registradas: {len(anomalias)}" + (f"  -> {log.relative_to(BASE)}" if log else ""))
    for tipo, descricao in anomalias:
        print(f"  - [{tipo}] {descricao}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Extrai o Report do Calypso e grava no SQLite")
    p.add_argument("--registro", default=str(BASE / "dados" / "04__Registro_MMC.xlsx"),
                   help="arquivo com a aba Report do Calypso")
    p.add_argument("--cadastros", default=str(BASE / "dados" / "03__Planilha_MMC.xlsm"),
                   help="arquivo com a aba BANCO DE DADOS completa")
    p.add_argument("--padroes", default=str(BASE / "dados" / "padroes.csv"))
    p.add_argument("--complemento", default=str(BASE / "dados" / "complemento_00091-2026.json"),
                   help="dados do certificado que não existem no Calypso (RF-04)")
    p.add_argument("--banco", default=str(BASE / "schulz_calibracao.db"))
    p.add_argument("--recriar", action="store_true", help="apaga o banco antes (somente para testes)")
    p.add_argument("--somente-estrutura", action="store_true", help="cria apenas as 3 tabelas vazias")
    args = p.parse_args()
    try:
        if args.somente_estrutura:
            abrir_banco(Path(args.banco), args.recriar).close()
            print(f"Estrutura criada: {Path(args.banco).name}")
            return 0
        return importar(args)
    except (ErroImportacao, sqlite3.DatabaseError, FileNotFoundError, KeyError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
