"""
Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A.
Fase 04 - MVP final: ingestão das planilhas, emissão do certificado e interface de operação.

Este é o arquivo que vira o executável Schulz_Calibracao.exe. Ele junta as entregas anteriores:
    Fase 01 -> regras de higienização (TR), de negócio (RN) e requisitos (RF)
    Fase 02 -> banco SQLite (03_banco.sql) e leitura do Calypso (extrator.py)
    Fase 03 -> classes de domínio e repositórios (04_classes.py)

Organização:
  1. Caminhos e carga dos módulos das fases anteriores (funciona no Python e no .exe)
  2. DadosComplemento: os dados do certificado que não vêm do Calypso, com saneamento (RF-04)
  3. ServicoCalibracao: módulo de ingestão (ETL) e motor de emissão (consulta com JOIN)
  4. GeradorCertificadoPDF: certificado no layout do modelo 07 da Schulz (RF-11, RN-27)
  5. PlanoDeTestes: casos de teste aprovados/reprovados, executáveis pelo próprio .exe
  6. JanelaPrincipal: interface gráfica (tkinter) para o técnico de metrologia
  7. main(): escolhe entre a interface e os modos de linha de comando

Uso:
    python 07_gerador_certificado.py                      # abre a interface
    python 07_gerador_certificado.py --testes             # roda o plano de testes e grava as evidências
    python 07_gerador_certificado.py --importar REGISTRO.xlsx --complemento DADOS.json --emitir
    python 07_gerador_certificado.py --emitir-certificado 00091/2026
    python 07_gerador_certificado.py --listar
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

VERSAO = "1.0.0"
VERSAO_ALGORITMO = "calc-1.0 (média de 2 medições, faixa de guarda com U em mm, margem 1e-7)"  # RN-18 / TR-25
NOME_APP = "Schulz_Calibracao"

# ======================================================================
# 1. CAMINHOS E MÓDULOS DAS FASES ANTERIORES
# ======================================================================
CONGELADO = bool(getattr(sys, "frozen", False))
# RECURSOS: arquivos que vão "dentro" do executável (DDL, cadastros, dados de teste)
RECURSOS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# PASTA_APP: onde o usuário enxerga os resultados (banco, certificados, logs), ao lado do .exe
PASTA_APP = Path(sys.executable).resolve().parent if CONGELADO else Path(__file__).resolve().parent
DADOS = RECURSOS / "dados"

# No .exe sem console (--windowed) não existe stdout; evita erro em qualquer print
if sys.stdout is None or sys.stderr is None:
    _log_console = open(PASTA_APP / "saida_console.log", "a", encoding="utf-8")
    sys.stdout = sys.stdout or _log_console
    sys.stderr = sys.stderr or _log_console
for _fluxo in (sys.stdout, sys.stderr):  # acentos no console do Windows
    if hasattr(_fluxo, "reconfigure"):
        try:
            _fluxo.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _carregar_modulo(nome: str):
    """Importa um módulo da pasta do projeto. '04_classes' começa com número, então não dá para
    usar 'import'. No executável o PyInstaller já embute o módulo; se não achar, carrega do .py."""
    try:
        return importlib.import_module(nome)
    except ModuleNotFoundError:
        caminho = RECURSOS / f"{nome}.py"
        spec = importlib.util.spec_from_file_location(nome, caminho)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules[nome] = modulo
        spec.loader.exec_module(modulo)
        return modulo


poo = _carregar_modulo("04_classes")      # Fase 03: classes de domínio e repositórios
extrator = _carregar_modulo("extrator")   # Fase 02: leitura do Calypso e funções TR
ARQUIVO_DDL = RECURSOS / "03_banco.sql"


def agora_txt() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def usuario_atual() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - em alguns Windows corporativos a variável não existe
        return "desconhecido"


def abrir_no_sistema(caminho: Path) -> None:
    """Abre um arquivo ou pasta no programa padrão (PDF no leitor, pasta no Explorer)."""
    if sys.platform.startswith("win"):
        os.startfile(str(caminho))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(caminho)])
    else:
        subprocess.Popen(["xdg-open", str(caminho)])


# ======================================================================
# 2. DADOS COMPLEMENTARES (RF-04) E SANEAMENTO (TR-01 a TR-13)
# ======================================================================
@dataclass
class DadosComplemento:
    """O que o Calypso não traz e o técnico informa na tela (antes vinha de um JSON)."""

    numero_certificado: str = ""
    data_emissao: str = ""
    codigo_item: str = ""
    denominacao: str = ""
    modelo: str = ""
    fabricante: str = ""
    numero_serie: str = ""
    resolucao: str = ""
    unidade: str = "mm"
    proxima_calibracao_item: str = ""
    solicitante: str = ""
    tecnico: str = ""
    matricula: str = ""
    signatario: str = ""
    padrao: str = ""
    temperatura: str = ""
    incerteza_um: str = ""
    fator_k: str = ""
    procedimento: str = "Calibração de dispositivo de controle - ITL-0026 (revisão 2)"

    # rótulo, obrigatório?, dica  -> usado na interface e nas mensagens
    ROTULOS = {
        "numero_certificado": ("Nº do certificado", True, "00091/2026"),
        "data_emissao": ("Data de emissão", True, "DD/MM/AAAA"),
        "codigo_item": ("Código do item", False, "vazio = pega do plano do Calypso"),
        "denominacao": ("Denominação do item", True, "Dispositivo de controle"),
        "modelo": ("Modelo", False, "Não se aplica"),
        "fabricante": ("Fabricante", False, "Não consta"),
        "numero_serie": ("Nº de série", False, "---"),
        "resolucao": ("Resolução (mm)", False, "0,001"),
        "unidade": ("Unidade", True, "mm ou um"),
        "proxima_calibracao_item": ("Próxima calibração do item", True, "DD/MM/AAAA"),
        "solicitante": ("Área solicitante", True, "Usinagem I"),
        "tecnico": ("Técnico executante", True, "nome completo"),
        "matricula": ("Matrícula do técnico", True, "16858-0"),
        "signatario": ("Signatário autorizado", True, "quem aprova"),
        "padrao": ("Padrão (MMC)", True, "500.011"),
        "temperatura": ("Temperatura ambiente (°C)", True, "20,4"),
        "incerteza_um": ("Incerteza expandida (µm)", True, "2,0769"),
        "fator_k": ("Fator k", True, "2,23"),
        "procedimento": ("Procedimento / revisão", False, "ITL-0026 (revisão 2)"),
    }

    @classmethod
    def campos(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    @classmethod
    def de_dict(cls, dados: dict) -> "DadosComplemento":
        """Aceita o JSON da Fase 02 (complemento_00091-2026.json); chaves com '_' são ignoradas."""
        valores = {}
        for nome in cls.campos():
            if nome in dados and dados[nome] is not None:
                valor = dados[nome]
                # números do JSON viram texto com vírgula, igual ao que o técnico digitaria
                valores[nome] = str(valor).replace(".", ",") if isinstance(valor, float) else str(valor)
        return cls(**valores)

    @classmethod
    def de_arquivo(cls, caminho: Path) -> "DadosComplemento":
        return cls.de_dict(json.loads(Path(caminho).read_text(encoding="utf-8")))

    def para_dict(self) -> dict:
        return asdict(self)

    def salvar(self, caminho: Path) -> None:
        Path(caminho).write_text(json.dumps(self.para_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def sanear(self) -> dict:
        """Aplica as regras TR e devolve os valores já no tipo certo.

        Junta TODOS os problemas numa mensagem só (o técnico corrige tudo de uma vez) e
        levanta ErroValidacao com a regra RN-01 quando falta campo obrigatório.
        """
        erros: list[str] = []
        v: dict = {}

        def texto(nome):
            valor = extrator.limpar_texto(getattr(self, nome))      # TR-01, TR-02, TR-06
            if valor is None and self.ROTULOS[nome][1]:
                erros.append(f"{self.ROTULOS[nome][0]}: campo obrigatório (RN-01)")
            return valor

        def data(nome):
            try:
                valor = extrator.para_data_iso(getattr(self, nome))  # TR-09
            except extrator.ErroImportacao as erro:
                erros.append(f"{self.ROTULOS[nome][0]}: {erro}")
                return None
            if valor is None and self.ROTULOS[nome][1]:
                erros.append(f"{self.ROTULOS[nome][0]}: campo obrigatório (RN-01)")
            return valor

        def numero(nome):
            try:
                valor = extrator.para_real(getattr(self, nome))      # TR-12 (vírgula decimal)
            except extrator.ErroImportacao as erro:
                erros.append(f"{self.ROTULOS[nome][0]}: {erro}")
                return None
            if valor is None and self.ROTULOS[nome][1]:
                erros.append(f"{self.ROTULOS[nome][0]}: campo obrigatório (RN-01)")
            return valor

        v["numero_certificado"] = texto("numero_certificado")
        if v["numero_certificado"] and not poo.PADRAO_CERTIFICADO.fullmatch(v["numero_certificado"]):
            erros.append(f"Nº do certificado: '{v['numero_certificado']}' fora do formato 00091/2026 (RN-23)")
        v["data_emissao"] = data("data_emissao")
        v["codigo_item"] = extrator.codigo_padrao(self.codigo_item) if extrator.limpar_texto(self.codigo_item) else None
        for nome in ("denominacao", "modelo", "fabricante", "numero_serie", "solicitante", "tecnico",
                     "matricula", "signatario", "procedimento"):
            v[nome] = texto(nome)
        v["unidade"] = texto("unidade")
        if v["unidade"] and v["unidade"] not in poo.UNIDADES:
            erros.append(f"Unidade: '{v['unidade']}' fora do domínio aprovado {poo.UNIDADES} (RN-06)")
        v["resolucao"] = numero("resolucao")
        v["proxima_calibracao_item"] = data("proxima_calibracao_item")
        v["padrao"] = extrator.codigo_padrao(self.padrao)             # TR-11: 500011 -> 500.011
        if not v["padrao"]:
            erros.append("Padrão (MMC): campo obrigatório (RN-01)")
        v["temperatura"] = numero("temperatura")
        if v["temperatura"] is not None and not 10 <= v["temperatura"] <= 30:
            erros.append(f"Temperatura ambiente: {v['temperatura']} °C fora da faixa 10 a 30 °C")
        incerteza = numero("incerteza_um")
        if incerteza is not None and incerteza < 0:
            erros.append("Incerteza expandida não pode ser negativa")
        # mesma regra da planilha: ARRED(G58/1000; 3) -> U em mm com 3 casas (achado A-09)
        v["incerteza_mm"] = round(incerteza / 1000, 3) if incerteza is not None else None
        v["fator_k"] = numero("fator_k")
        if v["fator_k"] is not None and v["fator_k"] <= 0:
            erros.append("Fator k deve ser maior que zero")
        if v.get("matricula"):
            try:
                poo.Tecnico("verificação", matricula=v["matricula"])   # RN-07 (formato 16858-0)
            except poo.ErroValidacao as erro:
                erros.append(str(erro))
        if v.get("data_emissao") and v.get("proxima_calibracao_item") and \
                v["proxima_calibracao_item"] <= v["data_emissao"]:
            erros.append("Próxima calibração do item deve ser depois da data de emissão")
        if erros:
            raise poo.ErroValidacao("Dados complementares com problema:\n  - " + "\n  - ".join(erros), "RN-01")
        return v


# ======================================================================
# 3. SERVIÇO: INGESTÃO (ETL) E MOTOR DE EMISSÃO
# ======================================================================
@dataclass
class ResultadoImportacao:
    numero_certificado: str
    parametros: int
    aprovados: int
    reprovados: int
    parecer: str
    anomalias: list = field(default_factory=list)
    arquivo_anomalias: Path | None = None


@dataclass
class ResultadoEmissao:
    numero_certificado: str
    parecer: str
    parametros: int
    aprovados: int
    reprovados: int
    pdf: Path
    txt: Path
    json: Path
    calibracao: object = None


class ServicoCalibracao:
    """Fachada usada pela interface, pela linha de comando e pelos testes.

    Ingestão:  planilha do Calypso + dados complementares -> saneamento -> objetos -> SQLite.
    Emissão:   SELECT com JOIN no SQLite -> objetos -> status APROVADO/REPROVADO -> PDF.
    """

    def __init__(self, pasta_trabalho: Path | str = PASTA_APP, caminho_banco: Path | str | None = None):
        self.pasta = Path(pasta_trabalho)
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.caminho_banco = Path(caminho_banco) if caminho_banco else self.pasta / "schulz_calibracao.db"
        self.pasta_certificados = self.pasta / "certificados"
        self.pasta_logs = self.pasta / "logs"
        self.arquivo_cadastros = DADOS / "03__Planilha_MMC.xlsm"
        self.arquivo_padroes = DADOS / "padroes.csv"

    # ------------------------------------------------------------------ util
    def _conexao(self) -> "poo.ConexaoSQLite":
        return poo.ConexaoSQLite(self.caminho_banco, ddl=ARQUIVO_DDL)

    def registrar_operacao(self, operacao: str, certificado: str, resultado: str, detalhe: str = "") -> None:
        """RF-14 / RNF-05: trilha de auditoria em CSV (quem, quando, o quê, resultado)."""
        self.pasta_logs.mkdir(parents=True, exist_ok=True)
        arquivo = self.pasta_logs / "operacoes.csv"
        novo = not arquivo.exists()
        with open(arquivo, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if novo:
                w.writerow(["data_hora", "usuario", "operacao", "certificado", "resultado", "detalhe", "versao"])
            w.writerow([agora_txt(), usuario_atual(), operacao, certificado, resultado,
                        " | ".join(" ".join(l.split()) for l in str(detalhe).splitlines() if l.strip()), VERSAO])

    def _salvar_anomalias(self, anomalias, certificado: str, arquivo: str, hash_arquivo: str) -> Path | None:
        """RF-18: tudo o que foi encontrado na leitura fica num CSV para conferência."""
        if not anomalias:
            return None
        self.pasta_logs.mkdir(parents=True, exist_ok=True)
        destino = self.pasta_logs / f"anomalias_{(certificado or 'sem-numero').replace('/', '-')}_" \
                                    f"{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(destino, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["data_hora", "certificado", "arquivo", "sha256", "tipo", "descricao"])
            for tipo, descricao in anomalias:
                w.writerow([agora_txt(), certificado, arquivo, hash_arquivo, tipo, descricao])
        return destino

    # ------------------------------------------------------------------ cadastros
    def garantir_cadastros(self, conexao) -> dict:
        """Carrega técnicos, áreas e padrões das planilhas da Schulz sem duplicar (RN-10)."""
        pessoas = poo.TecnicoRepositorio(conexao)
        equipamentos = poo.EquipamentoRepositorio(conexao)
        cadastros = extrator.ler_banco_de_dados(self.arquivo_cadastros)
        padroes_csv = {extrator.codigo_padrao(p["codigo"]): p for p in extrator.ler_padroes_csv(self.arquivo_padroes)}
        novos = {"tecnicos": 0, "areas": 0, "padroes": 0}
        with conexao.transacao():
            for nome in cadastros["metrologistas"]:
                if pessoas.buscar_por_nome(nome, "TECNICO") is None:
                    pessoas.inserir(poo.Tecnico(nome, setor="Metrologia", nome_abreviado=nome))
                    novos["tecnicos"] += 1
            for area in cadastros["areas"]:
                if pessoas.buscar_por_nome(area, "SOLICITANTE") is None:
                    pessoas.inserir(poo.Solicitante(area, setor=area))
                    novos["areas"] += 1
            for codigo in dict.fromkeys([*cadastros["padroes"], *padroes_csv]):
                p = padroes_csv.get(codigo)
                existente = equipamentos.buscar_por_codigo(codigo)
                if existente is None:
                    equipamentos.inserir(poo.Padrao(
                        codigo, p["denominacao"] if p else "Máquina de medição por coordenadas",
                        fabricante=p and p["fabricante"], modelo=p and p["modelo"],
                        numero_serie=p and p["numero_serie"], unidade=(p and p["unidade"]) or "mm",
                        resolucao=p and p["resolucao"], certificado_calibracao=p and p["certificado_calibracao"],
                        data_ultima_calibracao=p and p["data_ultima_calibracao"],
                        data_proxima_calibracao=p and p["data_proxima_calibracao"]))
                    novos["padroes"] += 1
                elif p and p["data_proxima_calibracao"]:
                    # padroes.csv atualizado (novo certificado do padrão, P-06): atualiza a validade
                    proxima = extrator.para_data_iso(p["data_proxima_calibracao"])
                    if (existente.data_proxima_calibracao or "") < proxima:
                        existente.registrar_calibracao(p["data_ultima_calibracao"], p["data_proxima_calibracao"],
                                                       p["certificado_calibracao"])
                        equipamentos.atualizar(existente)
        return novos

    def opcoes_cadastro(self) -> dict:
        """Listas para as caixas de seleção da tela (técnicos, signatários, áreas e padrões)."""
        with self._conexao() as conexao:
            self.garantir_cadastros(conexao)
            pessoas = poo.TecnicoRepositorio(conexao)
            equipamentos = poo.EquipamentoRepositorio(conexao)
            return {
                "tecnico": [p.nome for p in pessoas.listar("TECNICO") if p.ativo],
                "signatario": [p.nome for p in pessoas.listar("SIGNATARIO") if p.ativo],
                "solicitante": [p.nome for p in pessoas.listar("SOLICITANTE") if p.ativo],
                "padrao": [e.codigo_tag for e in equipamentos.listar("PADRAO") if e.ativo],
            }

    # ------------------------------------------------------------------ ingestão (ETL)
    def importar(self, arquivo_registro: Path | str, complemento: DadosComplemento) -> ResultadoImportacao:
        """Extrai o Report do Calypso, saneia, valida e grava em UMA transação (RN-29)."""
        registro = Path(arquivo_registro)
        numero = (complemento.numero_certificado or "").strip()
        try:
            resultado = self._importar(registro, complemento)
        except Exception as erro:
            self.registrar_operacao("IMPORTAR", numero, "RECUSADO", f"{registro.name}: {erro}")
            raise
        self.registrar_operacao("IMPORTAR", resultado.numero_certificado, "OK",
                                f"{registro.name}: {resultado.parametros} parâmetros, "
                                f"{len(resultado.anomalias)} anomalias, parecer {resultado.parecer}")
        return resultado

    def _importar(self, registro: Path, complemento: DadosComplemento) -> ResultadoImportacao:
        if not registro.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {registro}")
        if registro.suffix.lower() not in (".xlsx", ".xlsm"):
            raise poo.ErroValidacao(f"RF-02: formato '{registro.suffix}' não suportado; use a planilha "
                                    f"do Calypso (.xlsx ou .xlsm)", "RF-02")
        c = complemento.sanear()                                 # TR-01 a TR-13 no que o técnico digitou
        relatorio = extrator.ler_report(registro)                # EXT-03, RF-02, RF-03
        cab, anomalias = relatorio.cabecalho, list(relatorio.anomalias)
        hash_arquivo = extrator.sha256(registro)

        # TR-07 / RN-01: valor vazio não vira zero; parâmetro incompleto bloqueia a importação
        incompletos = [a for a in anomalias if a[0] == "DQ-COMPLETUDE"]
        if incompletos:
            log = self._salvar_anomalias(anomalias, c["numero_certificado"], registro.name, hash_arquivo)
            raise poo.ErroValidacao(
                f"A planilha tem {len(incompletos)} resultado(s) sem valor (TR-07). A importação foi bloqueada "
                f"para não emitir certificado com dado faltando.\n  - "
                + "\n  - ".join(d for _, d in incompletos) + (f"\nDetalhes em: {log}" if log else ""), "RN-01")

        data_medicao = extrator.para_data_iso(cab.get("data"))
        if not data_medicao:
            raise poo.ErroValidacao("A aba Report não tem a data da medição (campo 'Date')", "RN-01")
        if c["data_emissao"] < data_medicao:
            raise poo.ErroValidacao(f"Data de emissão {poo._data_br(c['data_emissao'])} é anterior à medição "
                                    f"{poo._data_br(data_medicao)}")
        codigo_item = c["codigo_item"] or (extrator.limpar_texto(cab.get("plano_medicao")) or "").split("_")[0]
        if not codigo_item:
            raise poo.ErroValidacao("Informe o código do item (o plano do Calypso não traz o código)", "RN-01")

        # divergências que não bloqueiam, mas ficam registradas (RF-09, RF-18)
        mmc = extrator.codigo_padrao(cab.get("mmc"))
        if mmc and mmc != c["padrao"]:
            anomalias.append(("DIVERGENCIA", f"MMC no Calypso = {mmc}, mas o certificado cita o padrão "
                                             f"{c['padrao']} (usado o informado na tela; pendência P-01)"))
        metrologista = extrator.limpar_texto(cab.get("metrologista"))
        if metrologista and extrator.chave_comparacao(metrologista) != extrator.chave_comparacao(c["tecnico"]):
            anomalias.append(("PADRONIZACAO", f"Metrologista no Calypso = '{metrologista}' x técnico informado = "
                                              f"'{c['tecnico']}' (relacionados pelo nome abreviado, A-06)"))

        with self._conexao() as conexao:
            pessoas = poo.TecnicoRepositorio(conexao)
            equipamentos = poo.EquipamentoRepositorio(conexao)
            medicoes = poo.MedicaoRepositorio(conexao)

            # RN-10 / RN-23: verifica duplicidade ANTES de qualquer gravação
            ja = medicoes.certificado_existe(c["numero_certificado"])
            if ja:
                raise poo.RegistroDuplicado(f"O certificado {c['numero_certificado']} já foi importado "
                                            f"({ja} parâmetros). Nada foi alterado.", "RN-23")
            mesmo_arquivo = conexao.consultar(
                "SELECT DISTINCT numero_certificado FROM medicao WHERE hash_arquivo = ?", (hash_arquivo,))
            if mesmo_arquivo:  # TR-19: o mesmo arquivo do Calypso não gera dois certificados
                raise poo.RegistroDuplicado(f"Este arquivo do Calypso (SHA-256 {hash_arquivo[:12]}...) já foi "
                                            f"importado no certificado {mesmo_arquivo[0][0]}.", "TR-19")

            with conexao.transacao():  # tudo ou nada (RN-29)
                self.garantir_cadastros(conexao)
                areas = [p.nome for p in pessoas.listar("SOLICITANTE")]

                # técnico: acha pelo nome completo ou pelo nome abreviado do Calypso (A-06)
                tecnico = (pessoas.buscar_por_nome(c["tecnico"], "TECNICO")
                           or (metrologista and pessoas.buscar_por_nome(metrologista, "TECNICO")))
                if tecnico is None:
                    tecnico = poo.Tecnico(c["tecnico"], setor="Metrologia", matricula=c["matricula"],
                                          nome_abreviado=metrologista)
                    pessoas.inserir(tecnico)
                    anomalias.append(("CADASTRO", f"Técnico '{c['tecnico']}' não estava cadastrado; incluído"))
                elif (tecnico.nome, tecnico.matricula) != (c["tecnico"], c["matricula"]):
                    if tecnico.matricula and tecnico.matricula != c["matricula"]:
                        raise poo.ErroValidacao(f"O técnico '{tecnico.nome}' já tem a matrícula {tecnico.matricula}, "
                                                f"diferente da informada ({c['matricula']})", "RN-07")
                    tecnico.nome, tecnico.matricula = c["tecnico"], c["matricula"]
                    pessoas.atualizar(tecnico)
                if not tecnico.ativo:
                    raise poo.ErroValidacao(f"Técnico {tecnico.nome} está inativo")

                if extrator.chave_comparacao(c["solicitante"]) not in {extrator.chave_comparacao(a) for a in areas}:
                    anomalias.append(("CADASTRO", f"Área '{c['solicitante']}' não está na lista do BANCO DE DADOS; "
                                                  f"cadastrada como nova área (confirmar com a Schulz, P-04)"))
                solicitante = pessoas.obter_ou_inserir(poo.Solicitante(c["solicitante"], setor=c["solicitante"]))
                signatario = pessoas.obter_ou_inserir(poo.Signatario(c["signatario"], setor="Metrologia"))
                if extrator.chave_comparacao(signatario.nome) == extrator.chave_comparacao(tecnico.nome):
                    raise poo.ErroValidacao("O signatário não pode ser o próprio técnico executante "
                                            "(segregação de funções)", "RN-14")

                padrao = equipamentos.buscar_por_codigo(c["padrao"])
                if not isinstance(padrao, poo.Padrao):
                    raise poo.ErroValidacao(f"Padrão {c['padrao']} não está cadastrado como padrão", "RN-05")

                item = equipamentos.buscar_por_codigo(codigo_item)
                if item is None:
                    item = poo.ItemCalibrado(codigo_item, c["denominacao"], fabricante=c["fabricante"],
                                             modelo=c["modelo"], numero_serie=c["numero_serie"],
                                             unidade=c["unidade"], resolucao=c["resolucao"], solicitante=solicitante)
                    equipamentos.inserir(item)
                elif not isinstance(item, poo.ItemCalibrado):
                    raise poo.ErroValidacao(f"O código {codigo_item} é de um padrão, não de um item", "RN-13")

                hora = cab.get("hora")
                calibracao = poo.Calibracao(
                    c["numero_certificado"], item=item, padrao=padrao, tecnico=tecnico, data_medicao=data_medicao,
                    solicitante=solicitante, signatario=None,
                    hora_medicao=hora.strftime("%H:%M:%S") if hasattr(hora, "strftime") else extrator.para_hora(hora),
                    plano_medicao=cab.get("plano_medicao"), fornecedor=cab.get("fornecedor"),
                    incerteza_expandida=c["incerteza_mm"], fator_k=c["fator_k"],
                    temperatura_ambiente=c["temperatura"], unidade=c["unidade"],
                    arquivo_origem=registro.name, hash_arquivo=hash_arquivo)
                for carac in relatorio.caracteristicas:
                    calibracao.nova_medicao(carac.parametro, carac.nominal, carac.tol_sup, carac.tol_inf,
                                            carac.medidas)
                medicoes.inserir_calibracao(calibracao)              # valida RN-05, RN-13 e grava
                medicoes.registrar_emissao(c["numero_certificado"], c["data_emissao"], signatario)

                # datas do item: só avança (não volta para uma calibração mais antiga)
                if (item.data_ultima_calibracao or "") <= c["data_emissao"]:
                    item.registrar_calibracao(c["data_emissao"], c["proxima_calibracao_item"],
                                              c["numero_certificado"])
                    if item.solicitante is None:
                        item.solicitante = solicitante
                    equipamentos.atualizar(item)

        log = self._salvar_anomalias(anomalias, c["numero_certificado"], registro.name, hash_arquivo)
        # guarda os dados digitados para o PDF (procedimento não existe no banco das 3 tabelas)
        self._salvar_complemento(c["numero_certificado"], complemento)
        r = calibracao.resumo()
        return ResultadoImportacao(c["numero_certificado"], r["parametros"], r["aprovados"], r["reprovados"],
                                   calibracao.parecer_final, anomalias, log)

    def _arquivo_complemento(self, numero: str) -> Path:
        return self.pasta / "complementos" / f"complemento_{numero.replace('/', '-')}.json"

    def _salvar_complemento(self, numero: str, complemento: DadosComplemento) -> None:
        destino = self._arquivo_complemento(numero)
        destino.parent.mkdir(parents=True, exist_ok=True)
        complemento.salvar(destino)

    # ------------------------------------------------------------------ motor de emissão
    def consultar(self, numero_certificado: str):
        """SELECT com JOIN (medicao x equipamento x tecnico_solicitante) e remontagem dos objetos."""
        with self._conexao() as conexao:
            return poo.MedicaoRepositorio(conexao).buscar_calibracao(numero_certificado)

    def emitir(self, numero_certificado: str, pasta_saida: Path | None = None) -> ResultadoEmissao:
        try:
            resultado = self._emitir(numero_certificado, pasta_saida)
        except Exception as erro:
            self.registrar_operacao("EMITIR", numero_certificado, "RECUSADO", str(erro))
            raise
        self.registrar_operacao("EMITIR", numero_certificado, resultado.parecer,
                                f"{resultado.pdf.name} ({resultado.aprovados} aprovados, "
                                f"{resultado.reprovados} reprovados)")
        return resultado

    def _emitir(self, numero_certificado: str, pasta_saida: Path | None) -> ResultadoEmissao:
        calibracao = self.consultar(numero_certificado)
        divergentes = [m.parametro for m in calibracao.medicoes if m.status_confere_com_banco is False]
        if divergentes:  # RF-09: o status gravado tem que bater com o recalculado
            raise poo.ErroValidacao(f"Status gravado diferente do recalculado em: {', '.join(divergentes)}. "
                                    f"Emissão bloqueada (RN-19).", "RN-19")
        if calibracao.signatario is None:
            raise poo.ErroValidacao("Certificado sem signatário autorizado: não pode ser emitido", "RN-13")
        complemento = None
        arq = self._arquivo_complemento(numero_certificado)
        if arq.exists():
            complemento = DadosComplemento.de_arquivo(arq)
        pasta = Path(pasta_saida or self.pasta_certificados)
        pasta.mkdir(parents=True, exist_ok=True)
        base = f"Certificado_{numero_certificado.replace('/', '-')}"
        pdf = GeradorCertificadoPDF(calibracao, complemento).gerar(pasta / f"{base}.pdf")
        txt = pasta / f"{base}.txt"
        txt.write_text(poo.RelatorioCalibracao(calibracao).texto() + "\n", encoding="utf-8")
        r = calibracao.resumo()
        resumo = {
            "certificado": numero_certificado, "parecer_final": calibracao.parecer_final,
            "parametros": r["parametros"], "aprovados": r["aprovados"], "reprovados": r["reprovados"],
            "regra_decisao": calibracao.regra_decisao, "incerteza_mm": calibracao.incerteza_expandida,
            "item": calibracao.item.codigo_tag, "padrao": calibracao.padrao.codigo_tag,
            "tecnico": calibracao.tecnico.nome, "signatario": calibracao.signatario.nome,
            "emitido_em": agora_txt(), "emitido_por": usuario_atual(), "versao_app": VERSAO,
            "versao_algoritmo": VERSAO_ALGORITMO, "arquivo_origem": calibracao.arquivo_origem,
            "sha256_origem": calibracao.hash_arquivo,
            "medicoes": [{"seq": m.sequencia, "parametro": m.parametro, "nominal": m.valor_nominal,
                          "tol_sup": m.tolerancia_superior, "tol_inf": m.tolerancia_inferior,
                          "medidas": list(m.medidas), "obtido": m.valor_obtido, "erro": m.erro,
                          "status": m.status} for m in calibracao.medicoes],
        }
        js = pasta / f"{base}_resultado.json"
        js.write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
        return ResultadoEmissao(numero_certificado, calibracao.parecer_final, r["parametros"], r["aprovados"],
                                r["reprovados"], pdf, txt, js, calibracao)

    def listar(self) -> list[dict]:
        """Certificados do banco com o parecer (JOIN + GROUP BY), para a tela principal (RF-16)."""
        if not self.caminho_banco.exists():
            return []
        with self._conexao() as conexao:
            linhas = conexao.consultar(
                """SELECT m.numero_certificado, MIN(m.data_medicao) AS data_medicao,
                          MIN(m.data_emissao) AS data_emissao, e.codigo AS item, e.denominacao,
                          t.nome AS tecnico, s.nome AS solicitante, COUNT(*) AS parametros,
                          SUM(m.status = 'APROVADO') AS aprovados, SUM(m.status = 'REPROVADO') AS reprovados
                   FROM medicao m
                   JOIN equipamento e              ON e.id_equipamento = m.id_equipamento
                   JOIN tecnico_solicitante t      ON t.id_pessoa = m.id_tecnico
                   LEFT JOIN tecnico_solicitante s ON s.id_pessoa = m.id_solicitante
                   GROUP BY m.numero_certificado
                   ORDER BY m.numero_certificado DESC""")
        saida = []
        for l in linhas:
            d = dict(l)
            d["parecer"] = "APROVADO" if d["reprovados"] == 0 else "REPROVADO"
            saida.append(d)
        return saida


# ======================================================================
# 4. CERTIFICADO EM PDF (layout do modelo 07 da Schulz)
# ======================================================================
class GeradorCertificadoPDF:
    """Monta o PDF com as seções 1 a 6 do modelo oficial e acrescenta o status de cada parâmetro."""

    def __init__(self, calibracao, complemento: DadosComplemento | None = None):
        self.c = calibracao
        self.comp = complemento

    def gerar(self, destino: Path) -> Path:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

        c = self.c
        num = poo._num_br
        azul = colors.HexColor("#1F3A5F")
        verde, vermelho = colors.HexColor("#1E7B34"), colors.HexColor("#B3261E")
        base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12)
        neg = ParagraphStyle("neg", parent=base, fontName="Helvetica-Bold")
        sec = ParagraphStyle("sec", parent=base, fontName="Helvetica-Bold", fontSize=10, leading=14,
                             spaceBefore=5, spaceAfter=2, textColor=azul)
        nota = ParagraphStyle("nota", parent=base, fontSize=7, leading=9)
        centro = ParagraphStyle("centro", parent=base, alignment=TA_CENTER)
        procedimento = (self.comp.procedimento if self.comp and self.comp.procedimento
                        else DadosComplemento.procedimento)
        fator_k = num(c.fator_k, 2)

        def cabecalho(canvas, doc):
            canvas.saveState()
            largura, altura = A4
            canvas.setFillColor(azul)
            canvas.setFont("Helvetica-Bold", 15)
            canvas.drawCentredString(largura / 2, altura - 18 * mm, "Certificado de Calibração")
            canvas.setFillColor(colors.black)
            canvas.setFont("Helvetica-Bold", 10)
            canvas.drawString(18 * mm, altura - 26 * mm, f"Certificado de Calibração nº {c.numero_certificado}")
            canvas.drawRightString(largura - 18 * mm, altura - 26 * mm, f"Data: {poo._data_br(c.data_emissao)}")
            canvas.setStrokeColor(azul)
            canvas.setLineWidth(1)
            canvas.line(18 * mm, altura - 29 * mm, largura - 18 * mm, altura - 29 * mm)
            # rodapé: rastreabilidade do arquivo de origem (RN-21) e versão do algoritmo (RN-18)
            canvas.setFont("Helvetica", 6.5)
            canvas.setFillColor(colors.HexColor("#555555"))
            canvas.line(18 * mm, 16 * mm, largura - 18 * mm, 16 * mm)
            canvas.drawString(18 * mm, 12.5 * mm,
                              f"Gerado por {NOME_APP} v{VERSAO} em {dt.datetime.now():%d/%m/%Y %H:%M} | "
                              f"origem: {c.arquivo_origem or '-'} | SHA-256 {(c.hash_arquivo or '-')[:16]}...")
            canvas.drawString(18 * mm, 9.5 * mm, "MVP acadêmico - Projeto Aplicado I (UniSENAI) com a Schulz S.A. "
                                                 "Documento de teste: sem valor oficial até a homologação (RN-28).")
            canvas.drawRightString(largura - 18 * mm, 12.5 * mm, f"Folha {doc.page:02d}")
            canvas.restoreState()

        doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                                topMargin=32 * mm, bottomMargin=20 * mm,
                                title=f"Certificado de Calibração {c.numero_certificado}",
                                author=f"{NOME_APP} v{VERSAO}")
        h = []
        h.append(Paragraph(f"<b>Solicitante:</b> {c.solicitante.nome if c.solicitante else '-'}", base))
        h.append(Paragraph("1. Descrição do instrumento calibrado", sec))
        t = Table([["Denominação:", c.item.descricao], ["Código:", c.item.codigo_tag],
                   ["Fabricante:", c.item.fabricante_exibicao()]], colWidths=[30 * mm, 140 * mm])
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                               ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        h.append(t)
        h.append(Paragraph("2. Padrão utilizado", sec))
        linha1, linha2 = c.padrao.texto_certificado().splitlines()
        h.append(Paragraph("*" + linha1, base))
        h.append(Paragraph(linha2.replace(";", f" - {c.padrao.fabricante_exibicao()};", 1), base))
        h.append(Paragraph("3. Procedimento de calibração", sec))
        h.append(Paragraph(f"Nr. do procedimento / revisão: {procedimento}.", base))
        n_med = max(len(m.medidas) for m in c.medicoes)
        h.append(Paragraph(
            "O instrumento foi medido na máquina de medição por coordenadas através de elementos geométricos "
            f"definidos, sendo o resultado apresentado a média de {'duas medições realizadas' if n_med == 2 else 'uma medição'}. "
            f"Os resultados estão referenciados a temperatura de 20° Celsius (temperatura ambiente na medição: "
            f"{num(c.temperatura_ambiente, 1)} °C, em {poo._data_br(c.data_medicao)}).", base))

        h.append(Paragraph(f"4. Resultados ({c.unidade})", sec))
        dados = [["Parâmetro avaliado", "Valor nominal", "Tol. +", "Tol. -", "Valor obtido", "Erro", "Situação*"]]
        estilo = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                  ("BACKGROUND", (0, 0), (-1, 0), azul), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0B7C3")),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6), ("TOPPADDING", (0, 0), (-1, -1), 1.6)]
        for i, m in enumerate(c.medicoes, start=1):
            dados.append([m.parametro, num(m.valor_nominal), num(m.tolerancia_superior),
                          num(m.tolerancia_inferior), num(m.valor_obtido), num(m.erro), m.status])
            cor = verde if m.aprovado else vermelho
            estilo += [("TEXTCOLOR", (6, i), (6, i), cor), ("FONT", (6, i), (6, i), "Helvetica-Bold", 8)]
            if i % 2 == 0:
                estilo.append(("BACKGROUND", (0, i), (5, i), colors.HexColor("#F2F4F7")))
        tab = Table(dados, colWidths=[40 * mm, 22 * mm, 17 * mm, 17 * mm, 22 * mm, 17 * mm, 25 * mm], repeatRows=1)
        tab.setStyle(TableStyle(estilo))
        h.append(tab)

        h.append(Paragraph("5. Incerteza de medição", sec))
        h.append(Paragraph(f"U = {num(c.incerteza_expandida)} {c.unidade} &nbsp;&nbsp;&nbsp; k = {fator_k}", base))

        h.append(Paragraph("6. Observações", sec))
        h.append(Paragraph("*Os parâmetros avaliados foram obtidos da Ficha de Auto Controle do produto que o "
                           "dispositivo controla.", base))
        r = c.resumo()
        aprovado = c.parecer_final == "APROVADO"
        cor_parecer = verde if aprovado else vermelho
        caixa = Table([[Paragraph(f"<font color='white'><b>RESULTADO DA CALIBRAÇÃO: {c.parecer_final}</b></font>",
                                  ParagraphStyle("p", parent=centro, fontSize=12, leading=15))],
                       [Paragraph(f"{r['parametros']} parâmetros avaliados | {r['aprovados']} dentro da tolerância | "
                                  f"{r['reprovados']} fora da tolerância", centro)]],
                      colWidths=[174 * mm])
        caixa.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), cor_parecer),
                                   ("BOX", (0, 0), (-1, -1), 1, cor_parecer),
                                   ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        h.append(Spacer(1, 4))
        h.append(caixa)
        regra = "faixa de guarda" if c.regra_decisao == "FAIXA_GUARDA" else "comparação simples"
        h.append(Spacer(1, 3))
        h.append(Paragraph(
            f"*Situação calculada pelo sistema com a regra de decisão por {regra}: o parâmetro é reprovado quando "
            f"(valor obtido ± U) ultrapassa o limite de tolerância. A coluna Situação e o resultado não constam do "
            f"modelo oficial; a regra definitiva depende da validação do responsável técnico (pendência P-03).", nota))

        assinatura = Table([
            [Paragraph(f"<b>Técnico executante:</b> {c.tecnico.nome}" +
                       (f" (matrícula {c.tecnico.matricula})" if c.tecnico.matricula else ""), base), ""],
            ["", "______________________________________"],
            ["", Paragraph(c.signatario.nome if c.signatario else "(pendente)", centro)],
            ["", Paragraph("Signatário Autorizado", centro)]], colWidths=[100 * mm, 74 * mm])
        assinatura.setStyle(TableStyle([("TOPPADDING", (0, 1), (-1, 1), 12), ("TOPPADDING", (0, 2), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("ALIGN", (1, 0), (1, -1), "CENTER"),
                                        ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        h.append(Spacer(1, 2))
        h.append(KeepTogether([assinatura]))
        h.append(Spacer(1, 4))
        notas = [
            f"Notas: * A incerteza expandida de medição relatada é declarada como incerteza padrão da medição "
            f"multiplicada pelo fator de abrangência k = {fator_k}, o qual para uma distribuição t corresponde a uma "
            f"probabilidade de abrangência de aproximadamente 95%. A incerteza padrão de medição foi determinada de "
            f"acordo com a publicação EA-4/02.",
            "* Os resultados deste certificado referem-se exclusivamente ao instrumento submetido a calibração, nas "
            "condições especificadas, não sendo extensivo a quaisquer lotes.",
            "* Não é permitida a reprodução parcial deste certificado.",
        ]
        h += [Paragraph(n, nota) for n in notas]
        doc.build(h, onFirstPage=cabecalho, onLaterPages=cabecalho)
        return destino


# ======================================================================
# 5. PLANO DE TESTES (roda no .exe com --testes ou pelo menu Ajuda)
# ======================================================================
class PlanoDeTestes:
    """Casos de teste documentados no 08_mvp_final_manual.md. Usa um banco separado só para teste."""

    def __init__(self, pasta_resultados: Path):
        self.pasta = Path(pasta_resultados)
        self.linhas: list[str] = []
        self.casos: list[dict] = []

    def _log(self, texto: str = "") -> None:
        self.linhas.append(texto)
        print(texto)

    @staticmethod
    def complemento_00091() -> DadosComplemento:
        return DadosComplemento.de_arquivo(DADOS / "complemento_00091-2026.json")

    def _caso(self, codigo: str, descricao: str, esperado: str, acao) -> None:
        self._log(f"\n[{codigo}] {descricao}")
        self._log(f"    Esperado: {esperado}")
        try:
            obtido, detalhe = acao()
        except poo.ErroSistema as erro:
            obtido, detalhe = f"RECUSADO ({erro.regra or type(erro).__name__})", str(erro)
        except extrator.ErroImportacao as erro:
            obtido, detalhe = "RECUSADO (EXT/RF-02)", str(erro)
        except Exception as erro:  # noqa: BLE001 - erro inesperado também entra no relatório
            obtido, detalhe = f"ERRO INESPERADO ({type(erro).__name__})", traceback.format_exc()
        passou = obtido == esperado
        self._log(f"    Obtido:   {obtido}")
        for linha in str(detalhe).splitlines():
            self._log(f"      {linha}")
        self._log(f"    => {'PASSOU' if passou else 'FALHOU'}")
        self.casos.append({"codigo": codigo, "descricao": descricao, "esperado": esperado,
                           "obtido": obtido, "passou": passou})

    def executar(self) -> int:
        if self.pasta.exists():
            shutil.rmtree(self.pasta)
        self.pasta.mkdir(parents=True)
        servico = ServicoCalibracao(self.pasta, self.pasta / "banco_teste.db")
        testes = DADOS / "testes"
        real = DADOS / "04__Registro_MMC.xlsx"
        self._log("=" * 96)
        self._log(f"PLANO DE TESTES - {NOME_APP} v{VERSAO} | {agora_txt()} | usuário {usuario_atual()}")
        self._log(f"Python {sys.version.split()[0]} | executável: {'sim' if CONGELADO else 'não'} | "
                  f"algoritmo: {VERSAO_ALGORITMO}")
        self._log("=" * 96)

        def importar_e_emitir(arquivo, comp):
            r = servico.importar(arquivo, comp)
            e = servico.emitir(r.numero_certificado)
            return (e.parecer, f"{r.numero_certificado}: {e.parametros} parâmetros, {e.aprovados} aprovados, "
                               f"{e.reprovados} reprovados | anomalias registradas: {len(r.anomalias)} | "
                               f"PDF: {e.pdf.name}")

        def comp_com(**mudancas):
            comp = self.complemento_00091()
            for k, v in mudancas.items():
                setattr(comp, k, v)
            return comp

        self._caso("CT-01", "Certificado real 00091/2026 (Report do Calypso da Schulz)",
                   "REPROVADO", lambda: importar_e_emitir(real, self.complemento_00091()))
        self._caso("CT-02", "Signatário igual ao técnico executante (segregação de funções)",
                   "RECUSADO (RN-14)", lambda: importar_e_emitir(
                       testes / "Registro_MMC_aprovado_00092.xlsx",
                       comp_com(numero_certificado="00098/2026", signatario="Jose Alcides Rosa Junior")))
        self._caso("CT-03", "Dispositivo com todas as posições dentro da tolerância (00092/2026)",
                   "APROVADO", lambda: importar_e_emitir(
                       testes / "Registro_MMC_aprovado_00092.xlsx",
                       comp_com(numero_certificado="00092/2026", data_emissao="21/08/2026")))
        self._caso("CT-04", "Importar de novo o certificado 00091/2026 (duplicidade)",
                   "RECUSADO (RN-23)", lambda: importar_e_emitir(real, self.complemento_00091()))
        self._caso("CT-05", "Mesmo arquivo do Calypso com outro número de certificado",
                   "RECUSADO (TR-19)", lambda: importar_e_emitir(real, comp_com(numero_certificado="00093/2026")))
        self._caso("CT-06", "Medição em 15/09/2026 com o padrão 500.011 vencido em 31/08/2026",
                   "RECUSADO (RN-05)", lambda: importar_e_emitir(
                       testes / "Registro_MMC_padrao_vencido.xlsx",
                       comp_com(numero_certificado="00094/2026", data_emissao="16/09/2026")))
        self._caso("CT-07", "Planilha sem a aba Report (fora do formato do Calypso)",
                   "RECUSADO (EXT/RF-02)", lambda: importar_e_emitir(
                       testes / "Registro_MMC_sem_report.xlsx", comp_com(numero_certificado="00095/2026")))
        self._caso("CT-08", "Planilha com um valor medido apagado (não pode virar zero)",
                   "RECUSADO (RN-01)", lambda: importar_e_emitir(
                       testes / "Registro_MMC_campo_vazio.xlsx",
                       comp_com(numero_certificado="00096/2026", data_emissao="22/08/2026")))
        self._caso("CT-09", "Dados complementares sem signatário e com número fora do padrão",
                   "RECUSADO (RN-01)", lambda: importar_e_emitir(
                       testes / "Registro_MMC_aprovado_00092.xlsx", comp_com(numero_certificado="97/26", signatario="")))


        def saneamento():
            comp = comp_com(solicitante="  Usinagem    I ", fabricante="Não consta", padrao="500011",
                            temperatura="20,4 °C", incerteza_um="2,0768977382997")
            v = comp.sanear()
            ok = (v["solicitante"], v["fabricante"], v["padrao"], v["temperatura"], v["incerteza_mm"]) == \
                 ("Usinagem I", None, "500.011", 20.4, 0.002)
            return ("SANEADO" if ok else "DIFERENTE",
                    f"solicitante={v['solicitante']!r} fabricante={v['fabricante']!r} padrao={v['padrao']!r} "
                    f"temperatura={v['temperatura']} incerteza_mm={v['incerteza_mm']}")
        self._caso("CT-10", "Saneamento: espaços repetidos, 'Não consta', código numérico e vírgula decimal",
                   "SANEADO", saneamento)

        def limite_exato():
            m = poo.Medicao("LIMITE", 0.7, 0.1, -0.1, [0.8], regra_decisao="SIMPLES")
            m2 = poo.Medicao("LIMITE+U", 6.075, 0.01, -0.01, [6.083, 6.083], incerteza_expandida=0.002)
            m3 = poo.Medicao("LIMITE+U", 6.075, 0.01, -0.01, [6.0835, 6.0835], incerteza_expandida=0.002)
            return (f"{m.status}", f"0,8 no limite (0,7+0,1) -> {m.status}; 6,083+U=6,085 -> {m2.status}; "
                                   f"6,0835+U=6,0855 -> {m3.status}")
        self._caso("CT-11", "Valor exatamente no limite (ponto flutuante) e faixa de guarda",
                   "APROVADO", limite_exato)

        def consulta_join():
            lista = servico.listar()
            texto = "; ".join(f"{l['numero_certificado']}={l['parecer']} ({l['aprovados']}/{l['parametros']})"
                              for l in lista)
            return ("2 CERTIFICADOS" if len(lista) == 2 else f"{len(lista)} CERTIFICADOS", texto)
        self._caso("CT-12", "Consulta com JOIN: só os certificados válidos ficaram no banco",
                   "2 CERTIFICADOS", consulta_join)

        def conferir_modelo():
            """Valores obtidos do 00091/2026 iguais aos do certificado modelo 07 (3 casas)."""
            modelo = ["0,002", "0,027", "6,079", "0,430", "6,078", "0,245", "16,968", "0,959", "16,970",
                      "0,236", "6,346", "0,132", "6,349", "0,868"]
            cal = servico.consultar("00091/2026")
            obtidos = [poo._num_br(m.valor_obtido) for m in cal.medicoes]
            iguais = sum(a == b for a, b in zip(obtidos, modelo))
            return ("CONFERE" if iguais == len(modelo) else "DIVERGE",
                    f"{iguais} de {len(modelo)} valores obtidos iguais ao certificado modelo da Schulz")
        self._caso("CT-13", "Fidelidade: valores do 00091/2026 x certificado modelo 07 da Schulz",
                   "CONFERE", conferir_modelo)

        passou = sum(c["passou"] for c in self.casos)
        self._log("\n" + "=" * 96)
        self._log(f"RESULTADO: {passou} de {len(self.casos)} casos de teste passaram")
        self._log("=" * 96)
        (self.pasta / "relatorio_testes.txt").write_text("\n".join(self.linhas) + "\n", encoding="utf-8")
        with open(self.pasta / "casos_de_teste.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["codigo", "descricao", "esperado", "obtido", "passou"], delimiter=";")
            w.writeheader()
            w.writerows(self.casos)
        return 0 if passou == len(self.casos) else 1


# ======================================================================
# 6. INTERFACE GRÁFICA (tkinter, já vem com o Python do Windows)
# ======================================================================
def iniciar_interface(servico: ServicoCalibracao) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    VERDE, VERMELHO, AZUL, CINZA = "#1E7B34", "#B3261E", "#1F3A5F", "#F4F6F9"

    class JanelaPrincipal(tk.Tk):
        def __init__(self):
            super().__init__()
            self.servico = servico
            self.title(f"Schulz S.A. - Emissão de Certificados de Calibração  (MVP v{VERSAO})")
            self.geometry("1280x800")
            self.minsize(1100, 680)
            if sys.platform.startswith("win"):
                self.state("zoomed")  # abre maximizada (telas de notebook têm pouca altura)
            self.configure(bg=CINZA)
            self.var_arquivo = tk.StringVar()
            self.vars: dict[str, tk.StringVar] = {n: tk.StringVar() for n in DadosComplemento.campos()}
            self.vars["unidade"].set("mm")
            self.vars["procedimento"].set(DadosComplemento.procedimento)
            self.combos: dict[str, ttk.Combobox] = {}
            self.certificado_atual: str | None = None
            self._estilos()
            self._menu()
            self._montar()
            self._carregar_opcoes()
            self.atualizar_lista()

        # ---------------------------------------------------------- layout
        def _estilos(self):
            st = ttk.Style(self)
            try:
                st.theme_use("vista" if sys.platform.startswith("win") else "clam")
            except tk.TclError:
                st.theme_use("clam")
            st.configure("Titulo.TLabel", font=("Segoe UI", 15, "bold"), foreground=AZUL, background=CINZA)
            st.configure("Sub.TLabel", font=("Segoe UI", 9), foreground="#444", background=CINZA)
            st.configure("Passo.TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground=AZUL)
            st.configure("Acao.TButton", font=("Segoe UI", 10, "bold"), padding=(12, 6))
            st.configure("Treeview", rowheight=20, font=("Segoe UI", 9))
            st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

        def _menu(self):
            barra = tk.Menu(self)
            arq = tk.Menu(barra, tearoff=False)
            arq.add_command(label="Abrir planilha do Calypso...", command=self.escolher_arquivo)
            arq.add_command(label="Carregar dados complementares (JSON)...", command=self.carregar_json)
            arq.add_command(label="Salvar dados complementares (JSON)...", command=self.salvar_json)
            arq.add_separator()
            arq.add_command(label="Abrir pasta de certificados", command=lambda: self._abrir(self.servico.pasta_certificados))
            arq.add_command(label="Abrir pasta de logs", command=lambda: self._abrir(self.servico.pasta_logs))
            arq.add_separator()
            arq.add_command(label="Sair", command=self.destroy)
            barra.add_cascade(label="Arquivo", menu=arq)
            aj = tk.Menu(barra, tearoff=False)
            aj.add_command(label="Preencher exemplo 00091/2026", command=self.exemplo)
            aj.add_command(label="Executar plano de testes", command=self.rodar_testes)
            aj.add_command(label="Sobre", command=self.sobre)
            barra.add_cascade(label="Ajuda", menu=aj)
            self.config(menu=barra)

        def _montar(self):
            topo = tk.Frame(self, bg=CINZA)
            topo.pack(fill="x", padx=14, pady=(10, 4))
            ttk.Label(topo, text="Emissão de Certificados de Calibração", style="Titulo.TLabel").pack(anchor="w")
            ttk.Label(topo, text="1) escolha a planilha do Calypso  ->  2) confira os dados complementares  ->  "
                                 "3) importe  ->  4) emita o certificado", style="Sub.TLabel").pack(anchor="w")

            corpo = ttk.PanedWindow(self, orient="horizontal")
            corpo.pack(fill="both", expand=True, padx=14, pady=6)
            esquerda = ttk.Frame(corpo, padding=4)
            direita = ttk.Frame(corpo, padding=4)
            corpo.add(esquerda, weight=2)
            corpo.add(direita, weight=3)

            # passo 1
            p1 = ttk.LabelFrame(esquerda, text=" 1. Planilha do Calypso (aba Report) ", style="Passo.TLabelframe",
                                padding=8)
            p1.pack(fill="x")
            ttk.Entry(p1, textvariable=self.var_arquivo).pack(side="left", fill="x", expand=True)
            ttk.Button(p1, text="Procurar...", command=self.escolher_arquivo).pack(side="left", padx=(6, 0))

            # passo 2
            p2 = ttk.LabelFrame(esquerda, text=" 2. Dados complementares (não vêm do Calypso) ",
                                style="Passo.TLabelframe", padding=8)
            p2.pack(fill="both", expand=True, pady=8)
            combos = {"solicitante", "tecnico", "signatario", "padrao", "unidade"}
            for i, nome in enumerate(DadosComplemento.campos()):
                rotulo, obrigatorio, dica = DadosComplemento.ROTULOS[nome]
                ttk.Label(p2, text=rotulo + (" *" if obrigatorio else "")).grid(row=i, column=0, sticky="w", pady=1)
                if nome in combos:
                    w = ttk.Combobox(p2, textvariable=self.vars[nome], width=34,
                                     values=list(poo.UNIDADES) if nome == "unidade" else [])
                    self.combos[nome] = w
                else:
                    w = ttk.Entry(p2, textvariable=self.vars[nome], width=37)
                w.grid(row=i, column=1, sticky="ew", padx=(8, 0), pady=1)
                ttk.Label(p2, text=dica, foreground="#888").grid(row=i, column=2, sticky="w", padx=(6, 0))
            p2.columnconfigure(1, weight=1)
            botoes2 = ttk.Frame(p2)
            botoes2.grid(row=len(DadosComplemento.campos()), column=0, columnspan=3, sticky="w", pady=(8, 0))
            ttk.Button(botoes2, text="Carregar JSON...", command=self.carregar_json).pack(side="left")
            ttk.Button(botoes2, text="Salvar JSON...", command=self.salvar_json).pack(side="left", padx=4)
            ttk.Button(botoes2, text="Limpar", command=self.limpar).pack(side="left")
            ttk.Label(p2, text="* obrigatório", foreground="#888").grid(
                row=len(DadosComplemento.campos()) + 1, column=0, sticky="w")

            # passos 3 e 4
            p3 = ttk.Frame(esquerda)
            p3.pack(fill="x")
            ttk.Button(p3, text="3. Importar e validar", style="Acao.TButton",
                       command=self.importar).pack(side="left", fill="x", expand=True)
            ttk.Button(p3, text="4. Emitir certificado (PDF)", style="Acao.TButton",
                       command=self.emitir).pack(side="left", fill="x", expand=True, padx=(6, 0))

            # direita: resultado
            self.lbl_parecer = tk.Label(direita, text="Nenhum certificado selecionado", font=("Segoe UI", 18, "bold"),
                                        fg="white", bg="#8A94A6", pady=10)
            self.lbl_parecer.pack(fill="x")
            self.lbl_resumo = ttk.Label(direita, text="", font=("Segoe UI", 10))
            self.lbl_resumo.pack(anchor="w", pady=(4, 4))

            fr_med = ttk.LabelFrame(direita, text=" Resultados por parâmetro ", style="Passo.TLabelframe", padding=4)
            fr_med.pack(fill="both", expand=True)
            colunas = ("seq", "parametro", "nominal", "tol_sup", "tol_inf", "obtido", "erro", "status")
            titulos = ("Nº", "Parâmetro", "Nominal", "Tol. +", "Tol. -", "Obtido", "Erro", "Situação")
            larguras = (36, 170, 75, 60, 60, 75, 65, 95)
            self.tv_med = ttk.Treeview(fr_med, columns=colunas, show="headings", height=9)
            for col, tit, larg in zip(colunas, titulos, larguras):
                self.tv_med.heading(col, text=tit)
                self.tv_med.column(col, width=larg, anchor="w" if col == "parametro" else "center")
            self.tv_med.tag_configure("APROVADO", foreground=VERDE)
            self.tv_med.tag_configure("REPROVADO", foreground=VERMELHO, background="#FDECEA")
            self.tv_med.pack(fill="both", expand=True)

            fr_cert = ttk.LabelFrame(direita, text=" Certificados no banco (duplo clique para abrir) ",
                                     style="Passo.TLabelframe", padding=4)
            fr_cert.pack(fill="both", expand=True, pady=(6, 0))
            colunas = ("numero", "medicao", "emissao", "item", "tecnico", "aprov", "reprov", "parecer")
            titulos = ("Certificado", "Medição", "Emissão", "Item", "Técnico", "Aprov.", "Reprov.", "Parecer")
            larguras = (85, 75, 75, 70, 150, 50, 55, 90)
            self.tv_cert = ttk.Treeview(fr_cert, columns=colunas, show="headings", height=3)
            for col, tit, larg in zip(colunas, titulos, larguras):
                self.tv_cert.heading(col, text=tit)
                self.tv_cert.column(col, width=larg, anchor="w" if col == "tecnico" else "center")
            self.tv_cert.tag_configure("APROVADO", foreground=VERDE)
            self.tv_cert.tag_configure("REPROVADO", foreground=VERMELHO)
            self.tv_cert.bind("<<TreeviewSelect>>", self._selecionou)
            self.tv_cert.bind("<Double-1>", lambda _e: self.emitir())
            self.tv_cert.pack(fill="both", expand=True)

            fr_msg = ttk.LabelFrame(direita, text=" Mensagens e anomalias ", style="Passo.TLabelframe", padding=4)
            fr_msg.pack(fill="both", expand=True, pady=(6, 0))
            self.txt = tk.Text(fr_msg, height=5, wrap="word", font=("Consolas", 9), bg="white")
            self.txt.pack(fill="both", expand=True)

            self.status = tk.StringVar(value=f"Banco: {self.servico.caminho_banco}")
            ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w",
                      padding=(8, 2)).pack(fill="x", side="bottom")

        # ---------------------------------------------------------- ações
        def mensagem(self, texto: str, limpar: bool = False):
            if limpar:
                self.txt.delete("1.0", "end")
            self.txt.insert("end", texto + "\n")
            self.txt.see("end")

        def _abrir(self, pasta: Path):
            Path(pasta).mkdir(parents=True, exist_ok=True)
            abrir_no_sistema(Path(pasta))

        def _carregar_opcoes(self):
            try:
                opcoes = self.servico.opcoes_cadastro()
            except Exception as erro:  # noqa: BLE001
                self.mensagem(f"Não foi possível carregar os cadastros: {erro}")
                return
            for nome, valores in opcoes.items():
                if nome in self.combos:
                    self.combos[nome]["values"] = valores

        def escolher_arquivo(self):
            caminho = filedialog.askopenfilename(
                title="Planilha do Calypso", initialdir=str(PASTA_APP),
                filetypes=[("Planilhas Excel", "*.xlsx *.xlsm"), ("Todos os arquivos", "*.*")])
            if caminho:
                self.var_arquivo.set(caminho)

        def complemento(self) -> DadosComplemento:
            return DadosComplemento(**{n: v.get() for n, v in self.vars.items()})

        def preencher(self, comp: DadosComplemento):
            for nome, valor in comp.para_dict().items():
                self.vars[nome].set(valor or "")

        def carregar_json(self):
            caminho = filedialog.askopenfilename(title="Dados complementares", initialdir=str(PASTA_APP),
                                                 filetypes=[("JSON", "*.json")])
            if caminho:
                try:
                    self.preencher(DadosComplemento.de_arquivo(Path(caminho)))
                    self.mensagem(f"Dados complementares carregados de {Path(caminho).name}")
                except (OSError, ValueError) as erro:
                    messagebox.showerror("Arquivo inválido", str(erro))

        def salvar_json(self):
            caminho = filedialog.asksaveasfilename(title="Salvar dados complementares", defaultextension=".json",
                                                   filetypes=[("JSON", "*.json")])
            if caminho:
                self.complemento().salvar(Path(caminho))
                self.mensagem(f"Dados complementares salvos em {caminho}")

        def limpar(self):
            for nome, var in self.vars.items():
                var.set({"unidade": "mm", "procedimento": DadosComplemento.procedimento}.get(nome, ""))
            self.var_arquivo.set("")

        def exemplo(self):
            copia = PASTA_APP / "04__Registro_MMC.xlsx"   # cópia que acompanha o .exe na pasta de entrega
            self.var_arquivo.set(str(copia if copia.exists() else DADOS / "04__Registro_MMC.xlsx"))
            self.preencher(PlanoDeTestes.complemento_00091())
            self.mensagem("Exemplo do certificado 00091/2026 preenchido (dados reais da Schulz).", limpar=True)

        def importar(self):
            arquivo = self.var_arquivo.get().strip()
            if not arquivo:
                messagebox.showwarning("Falta o arquivo", "Escolha a planilha do Calypso (passo 1).")
                return
            self.mensagem(f"Importando {Path(arquivo).name} ...", limpar=True)
            self.config(cursor="watch")
            self.update_idletasks()
            try:
                r = self.servico.importar(arquivo, self.complemento())
            except (poo.ErroSistema, extrator.ErroImportacao, FileNotFoundError) as erro:
                self.mensagem(f"IMPORTAÇÃO RECUSADA:\n{erro}")
                messagebox.showerror("Importação recusada", str(erro))
                return
            except Exception as erro:  # noqa: BLE001
                self.mensagem(traceback.format_exc())
                messagebox.showerror("Erro inesperado", str(erro))
                return
            finally:
                self.config(cursor="")
            self.mensagem(f"OK: certificado {r.numero_certificado} gravado no banco com {r.parametros} parâmetros "
                          f"({r.aprovados} aprovados, {r.reprovados} reprovados).")
            if r.anomalias:
                self.mensagem(f"{len(r.anomalias)} anomalia(s) registrada(s) (não impedem a emissão):")
                for tipo, desc in r.anomalias:
                    self.mensagem(f"  [{tipo}] {desc}")
                if r.arquivo_anomalias:
                    self.mensagem(f"Arquivo: {r.arquivo_anomalias}")
            self._carregar_opcoes()
            self.atualizar_lista(selecionar=r.numero_certificado)
            self.mensagem("Confira os resultados ao lado e clique em '4. Emitir certificado (PDF)'.")

        def emitir(self):
            numero = self.certificado_atual or self.vars["numero_certificado"].get().strip()
            if not numero:
                messagebox.showwarning("Selecione", "Selecione um certificado na lista ou importe um novo.")
                return
            try:
                e = self.servico.emitir(numero)
            except (poo.ErroSistema, extrator.ErroImportacao) as erro:
                self.mensagem(f"EMISSÃO RECUSADA: {erro}")
                messagebox.showerror("Emissão recusada", str(erro))
                return
            except Exception as erro:  # noqa: BLE001
                self.mensagem(traceback.format_exc())
                messagebox.showerror("Erro inesperado", str(erro))
                return
            self._mostrar(e.calibracao)
            self.mensagem(f"Certificado {numero} emitido: {e.parecer}\n  PDF:  {e.pdf}\n  TXT:  {e.txt}\n"
                          f"  JSON: {e.json}")
            if messagebox.askyesno("Certificado emitido",
                                   f"Certificado {numero}\nRESULTADO: {e.parecer}\n\n"
                                   f"{e.aprovados} parâmetro(s) aprovado(s) e {e.reprovados} reprovado(s).\n\n"
                                   f"Deseja abrir o PDF agora?"):
                abrir_no_sistema(e.pdf)

        def atualizar_lista(self, selecionar: str | None = None):
            self.tv_cert.delete(*self.tv_cert.get_children())
            try:
                certificados = self.servico.listar()
            except Exception as erro:  # noqa: BLE001
                self.mensagem(f"Não foi possível ler o banco: {erro}")
                return
            for c in certificados:
                self.tv_cert.insert("", "end", iid=c["numero_certificado"], tags=(c["parecer"],), values=(
                    c["numero_certificado"], poo._data_br(c["data_medicao"]), poo._data_br(c["data_emissao"]),
                    c["item"], c["tecnico"], c["aprovados"], c["reprovados"], c["parecer"]))
            self.status.set(f"Banco: {self.servico.caminho_banco}   |   {len(certificados)} certificado(s)   |   "
                            f"certificados em: {self.servico.pasta_certificados}")
            if selecionar and self.tv_cert.exists(selecionar):
                self.tv_cert.selection_set(selecionar)
                self.tv_cert.see(selecionar)

        def _selecionou(self, _evento=None):
            sel = self.tv_cert.selection()
            if not sel:
                return
            self.certificado_atual = sel[0]
            try:
                self._mostrar(self.servico.consultar(sel[0]))
            except poo.ErroSistema as erro:
                self.mensagem(str(erro))

        def _mostrar(self, cal):
            self.tv_med.delete(*self.tv_med.get_children())
            for m in cal.medicoes:
                self.tv_med.insert("", "end", tags=(m.status,), values=(
                    m.sequencia, m.parametro, poo._num_br(m.valor_nominal), poo._num_br(m.tolerancia_superior),
                    poo._num_br(m.tolerancia_inferior), poo._num_br(m.valor_obtido), poo._num_br(m.erro), m.status))
            r = cal.resumo()
            cor = VERDE if cal.parecer_final == "APROVADO" else VERMELHO
            self.lbl_parecer.config(text=f"Certificado {cal.numero_certificado}:  {cal.parecer_final}", bg=cor)
            self.lbl_resumo.config(text=f"{cal.item.descricao} {cal.item.codigo_tag}  |  padrão {cal.padrao.codigo_tag}"
                                        f"  |  técnico {cal.tecnico.nome}  |  {r['aprovados']} aprovados, "
                                        f"{r['reprovados']} reprovados  |  U = {poo._num_br(cal.incerteza_expandida)} mm")

        def rodar_testes(self):
            pasta = self.servico.pasta / "resultados_testes"
            if not messagebox.askyesno("Plano de testes",
                                       f"Os testes usam um banco separado e gravam os resultados em:\n{pasta}\n\n"
                                       f"O banco de produção não é alterado. Executar?"):
                return
            self.config(cursor="watch")
            self.update_idletasks()
            try:
                codigo = PlanoDeTestes(pasta).executar()
            finally:
                self.config(cursor="")
            texto = (pasta / "relatorio_testes.txt").read_text(encoding="utf-8")
            self.mensagem(texto.splitlines()[-2], limpar=True)
            messagebox.showinfo("Plano de testes", texto.splitlines()[-2] +
                                ("" if codigo == 0 else "\n\nVeja relatorio_testes.txt"))
            abrir_no_sistema(pasta)

        def sobre(self):
            messagebox.showinfo("Sobre", f"{NOME_APP} v{VERSAO}\nAlgoritmo: {VERSAO_ALGORITMO}\n\n"
                                         f"Projeto Aplicado I 2026.2 - UniSENAI\nEmpresa parceira: Schulz S.A.\n\n"
                                         f"Banco: {self.servico.caminho_banco}")

    JanelaPrincipal().mainloop()
    return 0


# ======================================================================
# 7. ENTRADA DO PROGRAMA
# ======================================================================
def _imprimir_resultado(e: ResultadoEmissao) -> None:
    print(poo.RelatorioCalibracao(e.calibracao).texto())
    print(f"\nRESULTADO FINAL DA CALIBRAÇÃO {e.numero_certificado}: {e.parecer} "
          f"({e.aprovados} aprovados, {e.reprovados} reprovados)")
    print(f"PDF:  {e.pdf}\nTXT:  {e.txt}\nJSON: {e.json}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog=NOME_APP, description="Emissão de certificados de calibração - Schulz S.A.")
    p.add_argument("--pasta", default=str(PASTA_APP), help="pasta de trabalho (banco, certificados e logs)")
    p.add_argument("--importar", metavar="REGISTRO.xlsx", help="planilha do Calypso para importar")
    p.add_argument("--complemento", metavar="DADOS.json", help="dados complementares (RF-04)")
    p.add_argument("--emitir", action="store_true", help="emite o certificado logo depois de importar")
    p.add_argument("--emitir-certificado", metavar="NUMERO", help="emite um certificado que já está no banco")
    p.add_argument("--listar", action="store_true", help="lista os certificados do banco")
    p.add_argument("--testes", action="store_true", help="executa o plano de testes e grava as evidências")
    p.add_argument("--versao", action="store_true")
    a = p.parse_args(argv)
    servico = ServicoCalibracao(Path(a.pasta))
    try:
        if a.versao:
            print(f"{NOME_APP} {VERSAO} | {VERSAO_ALGORITMO}")
            return 0
        if a.testes:
            return PlanoDeTestes(Path(a.pasta) / "resultados_testes").executar()
        if a.listar:
            for c in servico.listar():
                print(f"{c['numero_certificado']}  medição {poo._data_br(c['data_medicao'])}  item {c['item']}  "
                      f"{c['aprovados']}/{c['parametros']} aprovados  -> {c['parecer']}")
            return 0
        if a.importar:
            comp = DadosComplemento.de_arquivo(Path(a.complemento)) if a.complemento else DadosComplemento()
            r = servico.importar(a.importar, comp)
            print(f"Importado {r.numero_certificado}: {r.parametros} parâmetros | {len(r.anomalias)} anomalias")
            for tipo, desc in r.anomalias:
                print(f"  [{tipo}] {desc}")
            if a.emitir:
                _imprimir_resultado(servico.emitir(r.numero_certificado))
            return 0
        if a.emitir_certificado:
            _imprimir_resultado(servico.emitir(a.emitir_certificado))
            return 0
        return iniciar_interface(servico)
    except (poo.ErroSistema, extrator.ErroImportacao, FileNotFoundError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
