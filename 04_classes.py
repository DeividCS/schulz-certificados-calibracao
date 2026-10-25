"""
Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A.
Fase 03 - MVP (Parte 1): classes do sistema (POO em Python) e conexão com o SQLite.

Organização do arquivo:
  1. Exceções: ErroValidacao (a própria classe recusou o dado, antes de ir pro banco) e
     ErroPersistencia com as subclasses (erro que veio do SQLite, traduzido para a regra de negócio).
  2. Classes de domínio:
     - Pessoa -> Tecnico, Signatario, Solicitante
     - Equipamento -> ItemCalibrado, Padrao
     - Medicao: um parâmetro do certificado; é aqui que fica a regra do status
     - Calibracao: o cabeçalho do certificado, que junta as medições
  3. Conexão e persistência: ConexaoSQLite e os repositórios (TecnicoRepositorio,
     EquipamentoRepositorio e MedicaoRepositorio) com INSERT, SELECT, UPDATE, DELETE e JOIN.
  4. RelatorioCalibracao: monta o texto do certificado para mostrar no terminal.

O banco continua o mesmo da Fase 02 (03_banco.sql, 3 tabelas). As regras são conferidas duas
vezes: nas classes, para dar uma mensagem clara antes de abrir a transação, e no banco (CHECK,
FK e triggers), que segura o dado errado mesmo se alguém gravar por fora do sistema.

Os nomes pedidos no roteiro foram mantidos nas classes:
    Tecnico.id_tecnico      -> coluna tecnico_solicitante.id_pessoa
    Equipamento.codigo_tag  -> coluna equipamento.codigo
    Equipamento.descricao   -> coluna equipamento.denominacao

Obs.: como o nome do arquivo começa com número, não dá para usar "import 04_classes".
Nos outros scripts usamos: importlib.import_module("04_classes")
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import statistics
import unicodedata
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

BASE = Path(__file__).resolve().parent
ARQUIVO_DDL = BASE / "03_banco.sql"
UNIDADES = ("mm", "um")                          # 'um' = micrometro (mesmo domínio do banco)
PADRAO_CERTIFICADO = re.compile(r"^\d{5}/\d{4}$")  # ex.: 00091/2026 (RN-23)
# Margem para erro de ponto flutuante na comparação com os limites (mesma ideia da Aula 02)
MARGEM_FLOAT = 1e-7
# TR-06: marcadores de "sem informação" usados nas planilhas viram nulo no banco
VALORES_NULOS = {"-", "--", "---", "N/A", "n/a", "Não consta", "Não se aplica", "Não informado"}


# ======================================================================
# 1. EXCEÇÕES
# ======================================================================
class ErroSistema(Exception):
    """Base de todos os erros previstos pelo sistema."""

    def __init__(self, mensagem: str, regra: str | None = None):
        super().__init__(mensagem)
        self.regra = regra


class ErroValidacao(ErroSistema):
    """Dado recusado pela própria classe, antes de qualquer comando SQL."""


class ErroPersistencia(ErroSistema):
    """Erro devolvido pelo SQLite, já traduzido para uma mensagem de negócio."""

    def __init__(self, mensagem: str, regra: str | None = None, original: str | None = None):
        super().__init__(mensagem, regra)
        self.original = original  # mensagem crua do SQLite, útil para auditoria


class RegistroDuplicado(ErroPersistencia):
    """UNIQUE violado: certificado, parâmetro, matrícula ou código já existentes (RN-10, RN-23)."""


class ReferenciaInvalida(ErroPersistencia):
    """FOREIGN KEY violada: cadastro inexistente ou exclusão de cadastro com histórico (RN-16, RN-22)."""


class RegraNegocioViolada(ErroPersistencia):
    """CHECK, NOT NULL ou trigger (RAISE) recusou a gravação."""


class RegistroNaoEncontrado(ErroPersistencia):
    """SELECT por chave não retornou linha."""


def traduzir_erro_sqlite(erro: sqlite3.Error, contexto: str) -> ErroPersistencia:
    """Converte a mensagem técnica do SQLite em uma exceção com significado de negócio.

    A mensagem original é preservada em `.original`. Quando a mensagem traz o código da regra
    (os triggers do 03_banco.sql começam por 'RN-xx:'), ele é copiado para `.regra`.
    """
    msg = str(erro)
    achou_regra = re.search(r"\bRN-\d{2}\b", msg)
    regra = achou_regra.group(0) if achou_regra else None
    if "UNIQUE constraint failed" in msg:
        campos = msg.split(":", 1)[1].strip()
        return RegistroDuplicado(f"{contexto}: já existe registro com o mesmo valor em ({campos})",
                                 regra or "RN-10", msg)
    if "FOREIGN KEY constraint failed" in msg:
        return ReferenciaInvalida(f"{contexto}: o registro aponta para um cadastro inexistente ou "
                                  f"possui histórico vinculado", regra or "RN-22", msg)
    if "NOT NULL constraint failed" in msg:
        campo = msg.split(":", 1)[1].strip()
        return RegraNegocioViolada(f"{contexto}: campo obrigatório ausente ({campo})", regra or "RN-01", msg)
    if "CHECK constraint failed" in msg:
        expressao = msg.split(":", 1)[1].strip()
        return RegraNegocioViolada(f"{contexto}: valor fora da regra do banco ({expressao})", regra, msg)
    if isinstance(erro, sqlite3.IntegrityError) or regra:
        # mensagens dos triggers (RAISE ABORT) chegam como IntegrityError com o texto da regra
        return RegraNegocioViolada(f"{contexto}: {msg}", regra, msg)
    if isinstance(erro, sqlite3.OperationalError):
        return ErroPersistencia(f"{contexto}: falha ao acessar o banco ({msg})", None, msg)
    return ErroPersistencia(f"{contexto}: {msg}", regra, msg)


# ======================================================================
# Funções auxiliares de validação (mesmas regras TR da Fase 01)
# ======================================================================
def _texto(valor, campo: str, obrigatorio: bool = True) -> str | None:
    """TR-01/TR-02: remove espaços nas pontas e repetidos. Vazio vira None (ou erro, se obrigatório)."""
    texto = " ".join(str(valor).split()) if valor is not None else ""
    if texto in VALORES_NULOS:
        texto = ""
    if not texto:
        if obrigatorio:
            raise ErroValidacao(f"{campo} é obrigatório", "RN-01")
        return None
    return texto


def _data_iso(valor, campo: str, obrigatorio: bool = True) -> str | None:
    """TR-09: aceita date, 'AAAA-MM-DD' ou 'DD/MM/AAAA' e devolve 'AAAA-MM-DD'."""
    if valor is None or valor == "":
        if obrigatorio:
            raise ErroValidacao(f"{campo} é obrigatória", "RN-01")
        return None
    if isinstance(valor, dt.datetime):
        return valor.date().isoformat()
    if isinstance(valor, dt.date):
        return valor.isoformat()
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(str(valor).strip(), formato).date().isoformat()
        except ValueError:
            pass
    raise ErroValidacao(f"{campo} inválida: {valor!r} (use AAAA-MM-DD ou DD/MM/AAAA)", "TR-09")


def _real(valor, campo: str, obrigatorio: bool = True) -> float | None:
    """TR-12: aceita número ou texto com vírgula decimal ('0,001'). Não troca vazio por zero (TR-07)."""
    if valor is None or valor == "":
        if obrigatorio:
            raise ErroValidacao(f"{campo} é obrigatório", "RN-01")
        return None
    if isinstance(valor, bool):
        raise ErroValidacao(f"{campo} deve ser numérico", "TR-12")
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace("°C", "").replace("°", "")
    if "," in texto and "." in texto:
        raise ErroValidacao(f"{campo}: número ambíguo (vírgula e ponto): {valor!r}", "TR-12")
    try:
        return float(texto.replace(",", "."))
    except ValueError:
        raise ErroValidacao(f"{campo} deve ser numérico: {valor!r}", "TR-12") from None


def _sem_acento(texto: str | None) -> str:
    """TR-05: versão minúscula e sem acento, usada somente para comparar nomes."""
    if not texto:
        return ""
    base = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(base.lower().split())


def _num_br(valor: float | None, casas: int = 3) -> str:
    """RN-08: arredonda somente na apresentação, com vírgula decimal (padrão do certificado)."""
    if valor is None:
        return "-"
    texto = f"{valor:.{casas}f}"
    return "0," + "0" * casas if texto.lstrip("-") == "0." + "0" * casas else texto.replace(".", ",")


def _data_br(iso: str | None) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else "-"


# ======================================================================
# 2. CLASSES DE DOMÍNIO
# ======================================================================
class EntidadeBase(ABC):
    """Comportamento comum: identificador gerado pelo banco e validação obrigatória.

    O id só é atribuído pelo repositório depois do INSERT (método _vincular_id), e uma única vez.
    """

    def __init__(self, id_: int | None = None):
        self._id = id_

    @property
    def id(self) -> int | None:
        return self._id

    @property
    def persistido(self) -> bool:
        return self._id is not None

    def _vincular_id(self, novo_id: int) -> None:
        if self._id is not None and self._id != novo_id:
            raise ErroValidacao(f"{type(self).__name__} já possui id {self._id}")
        self._id = novo_id

    @abstractmethod
    def validar(self) -> None:
        """Confere as regras da entidade; levanta ErroValidacao se algo estiver errado."""

    def __eq__(self, outro) -> bool:
        if type(self) is not type(outro):
            return NotImplemented
        if self.persistido and outro.persistido:
            return self._id == outro._id
        return self is outro

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._id if self.persistido else id(self)))


# ----------------------------------------------------------------------
# Pessoas (tabela tecnico_solicitante)
# ----------------------------------------------------------------------
class Pessoa(EntidadeBase):
    """Quem participa da calibração. A subclasse define o papel (coluna `tipo` do banco)."""

    TIPO: str = ""

    def __init__(self, nome: str, setor: str | None = None, matricula: str | None = None,
                 nome_abreviado: str | None = None, ativo: bool = True, id_: int | None = None):
        super().__init__(id_)
        self.nome = nome
        self.setor = setor
        self.matricula = matricula
        self.nome_abreviado = nome_abreviado
        self.ativo = ativo

    # --- encapsulamento: cada atributo passa por uma validação no setter ---
    @property
    def nome(self) -> str:
        return self._nome

    @nome.setter
    def nome(self, valor: str) -> None:
        self._nome = _texto(valor, "Nome")

    @property
    def setor(self) -> str | None:
        return self._setor

    @setor.setter
    def setor(self, valor: str | None) -> None:
        self._setor = _texto(valor, "Setor", obrigatorio=False)

    @property
    def matricula(self) -> str | None:
        return self._matricula

    @matricula.setter
    def matricula(self, valor) -> None:
        # RN-07: identificador é texto. Um número viria do Excel sem o hífen e sem zeros à esquerda.
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            raise ErroValidacao("Matrícula deve ser informada como texto (ex.: '16858-0')", "RN-07")
        texto = _texto(valor, "Matrícula", obrigatorio=False)
        if texto is not None and not re.fullmatch(r"[0-9]+(-[0-9A-Za-z])?", texto):
            raise ErroValidacao(f"Matrícula inválida: {texto!r} (formato esperado: 16858-0)", "RN-07")
        self._matricula = texto

    @property
    def nome_abreviado(self) -> str | None:
        return self._nome_abreviado

    @nome_abreviado.setter
    def nome_abreviado(self, valor: str | None) -> None:
        self._nome_abreviado = _texto(valor, "Nome abreviado", obrigatorio=False)

    @property
    def ativo(self) -> bool:
        return self._ativo

    @ativo.setter
    def ativo(self, valor: bool) -> None:
        self._ativo = bool(valor)

    @property
    def tipo(self) -> str:
        return self.TIPO

    def reconhece_nome(self, nome: str | None) -> bool:
        """Achado A-06: 'José A. Rosa Jr' (Calypso) e 'Jose Alcides Rosa Junior' são a mesma pessoa."""
        chave = _sem_acento(nome)
        return bool(chave) and chave in (_sem_acento(self.nome), _sem_acento(self.nome_abreviado))

    @abstractmethod
    def papel(self) -> str:
        """Texto do papel exibido no relatório (polimorfismo)."""

    def validar(self) -> None:
        _texto(self.nome, "Nome")
        if self.TIPO not in ("TECNICO", "SIGNATARIO", "SOLICITANTE"):
            raise ErroValidacao(f"Tipo de pessoa inválido: {self.TIPO!r}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id}, nome={self.nome!r})"


class Tecnico(Pessoa):
    """Profissional calibrador (metrologista) que executa a medição na MMC."""

    TIPO = "TECNICO"

    @property
    def id_tecnico(self) -> int | None:  # nome pedido no roteiro
        return self.id

    def papel(self) -> str:
        return "Técnico executante"


class Signatario(Pessoa):
    """Responsável que assina (aprova) o certificado. Papel separado do técnico (RN-13, RN-14)."""

    TIPO = "SIGNATARIO"

    def papel(self) -> str:
        return "Signatário autorizado"


class Solicitante(Pessoa):
    """Área da Schulz que solicita a calibração (ex.: Usinagem I)."""

    TIPO = "SOLICITANTE"

    def papel(self) -> str:
        return "Área solicitante"


# ----------------------------------------------------------------------
# Equipamentos (tabela equipamento)
# ----------------------------------------------------------------------
class Equipamento(EntidadeBase):
    """Instrumento de medição. A subclasse define a categoria (item calibrado ou padrão)."""

    CATEGORIA: str = ""

    def __init__(self, codigo_tag, descricao: str, fabricante: str | None = None,
                 modelo: str | None = None, numero_serie: str | None = None, unidade: str = "mm",
                 resolucao=None, certificado_calibracao: str | None = None,
                 data_ultima_calibracao=None, data_proxima_calibracao=None,
                 periodicidade_meses: int | None = None, ativo: bool = True, id_: int | None = None):
        super().__init__(id_)
        self.codigo_tag = codigo_tag
        self.descricao = descricao
        self.fabricante = fabricante
        self.modelo = _texto(modelo, "Modelo", obrigatorio=False)
        self.numero_serie = _texto(numero_serie, "Número de série", obrigatorio=False)
        self.unidade = unidade
        self.resolucao = resolucao
        self.certificado_calibracao = _texto(certificado_calibracao, "Certificado do equipamento", obrigatorio=False)
        self._data_ultima = _data_iso(data_ultima_calibracao, "Data da última calibração", False)
        self._data_proxima = _data_iso(data_proxima_calibracao, "Data da próxima calibração", False)
        self._checar_datas(self._data_ultima, self._data_proxima)
        self.periodicidade_meses = periodicidade_meses
        self.ativo = bool(ativo)

    @property
    def id_equipamento(self) -> int | None:  # nome pedido no roteiro
        return self.id

    @property
    def codigo_tag(self) -> str:
        return self._codigo_tag

    @codigo_tag.setter
    def codigo_tag(self, valor) -> None:
        # TR-11 / RN-07: o Calypso grava 500008 (número); o certificado usa '500.008' (texto).
        if isinstance(valor, float):
            raise ErroValidacao("Código do equipamento não pode ser número decimal "
                                "(500.011 viraria 500.01); informe como texto", "RN-07")
        texto = _texto(valor, "Código do equipamento")
        if texto.isdigit() and len(texto) == 6:
            texto = f"{texto[:3]}.{texto[3:]}"
        self._codigo_tag = texto

    @property
    def descricao(self) -> str:
        return self._descricao

    @descricao.setter
    def descricao(self, valor: str) -> None:
        self._descricao = _texto(valor, "Descrição (denominação)")

    @property
    def fabricante(self) -> str | None:
        return self._fabricante

    @fabricante.setter
    def fabricante(self, valor: str | None) -> None:
        # TR-06: "Não consta" vira nulo no banco; o texto volta só na apresentação
        self._fabricante = _texto(valor, "Fabricante", obrigatorio=False)

    @property
    def unidade(self) -> str:
        return self._unidade

    @unidade.setter
    def unidade(self, valor: str) -> None:
        valor = (valor or "mm").strip()
        if valor not in UNIDADES:  # RN-06
            raise ErroValidacao(f"Unidade {valor!r} fora do domínio aprovado {UNIDADES}", "RN-06")
        self._unidade = valor

    @property
    def resolucao(self) -> float | None:
        return self._resolucao

    @resolucao.setter
    def resolucao(self, valor) -> None:
        numero = _real(valor, "Resolução", obrigatorio=False)
        if numero is not None and numero <= 0:
            raise ErroValidacao("Resolução deve ser maior que zero")
        self._resolucao = numero

    @property
    def periodicidade_meses(self) -> int | None:
        return self._periodicidade

    @periodicidade_meses.setter
    def periodicidade_meses(self, valor: int | None) -> None:
        if valor is not None and (not isinstance(valor, int) or valor <= 0):
            raise ErroValidacao("Periodicidade deve ser um número inteiro de meses maior que zero")
        self._periodicidade = valor

    @property
    def data_ultima_calibracao(self) -> str | None:
        return self._data_ultima

    @property
    def data_proxima_calibracao(self) -> str | None:
        return self._data_proxima

    @staticmethod
    def _checar_datas(ultima: str | None, proxima: str | None) -> None:
        if ultima and proxima and proxima < ultima:
            raise ErroValidacao("Próxima calibração não pode ser anterior à última calibração")

    def registrar_calibracao(self, data_calibracao, data_proxima,
                             certificado: str | None = None) -> None:
        """Atualiza as datas de calibração do próprio equipamento (vira UPDATE no repositório)."""
        ultima = _data_iso(data_calibracao, "Data da calibração")
        proxima = _data_iso(data_proxima, "Data da próxima calibração")
        self._checar_datas(ultima, proxima)
        self._data_ultima, self._data_proxima = ultima, proxima
        if certificado is not None:
            self.certificado_calibracao = _texto(certificado, "Certificado")

    def fabricante_exibicao(self) -> str:
        return self.fabricante or "Não consta"

    def validar(self) -> None:
        _texto(self.codigo_tag, "Código do equipamento")
        if self.CATEGORIA not in ("ITEM_CALIBRADO", "PADRAO"):
            raise ErroValidacao(f"Categoria inválida: {self.CATEGORIA!r}")

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.id}, codigo_tag={self.codigo_tag!r})"


class ItemCalibrado(Equipamento):
    """Objeto do certificado (ex.: Dispositivo de controle 703.1208)."""

    CATEGORIA = "ITEM_CALIBRADO"

    def __init__(self, *args, solicitante: Solicitante | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.solicitante = solicitante

    @property
    def solicitante(self) -> Solicitante | None:
        return self._solicitante

    @solicitante.setter
    def solicitante(self, valor: Solicitante | None) -> None:
        if valor is not None and not isinstance(valor, Solicitante):
            raise ErroValidacao("O dono do item deve ser uma área solicitante")
        self._solicitante = valor


class Padrao(Equipamento):
    """Instrumento de referência (ex.: MMC Carl Zeiss 500.011). Só vale se estiver calibrado."""

    CATEGORIA = "PADRAO"

    def esta_valido_em(self, data) -> bool:
        """RN-05: válido se tem certificado e a próxima calibração não passou da data informada."""
        data_iso = _data_iso(data, "Data de referência")
        return bool(self.certificado_calibracao and self.data_proxima_calibracao
                    and data_iso <= self.data_proxima_calibracao)

    def validar_para_uso(self, data) -> None:
        if not self.ativo:
            raise ErroValidacao(f"Padrão {self.codigo_tag} está inativo", "RN-05")
        if not self.certificado_calibracao or not self.data_proxima_calibracao:
            raise ErroValidacao(f"Padrão {self.codigo_tag} sem certificado ou validade cadastrada", "RN-05")
        if not self.esta_valido_em(data):
            raise ErroValidacao(f"Padrão {self.codigo_tag} vencido em {_data_br(self.data_proxima_calibracao)} "
                                f"(medição em {_data_br(_data_iso(data, 'data'))})", "RN-05")

    def texto_certificado(self) -> str:
        """Seção 2 do certificado. Datas formatadas em Python, sem depender do idioma do Excel (A-08)."""
        linha1 = f"{self.descricao} {self.codigo_tag} - {self.fabricante_exibicao()}"
        linha2 = (f"Certificado de Calibração nº {self.certificado_calibracao or '-'}; de "
                  f"{_data_br(self.data_ultima_calibracao)} - Próxima Calibração: "
                  f"{_data_br(self.data_proxima_calibracao)}")
        return f"{linha1}\n{linha2}"


# ----------------------------------------------------------------------
# Medição e calibração
# ----------------------------------------------------------------------
class Medicao(EntidadeBase):
    """Um parâmetro avaliado no certificado (ex.: '62P_DIÂMETRO 1').

    Os valores medidos só são definidos no construtor e não têm setter: depois de criada, a
    medição é imutável (RN-02). Valor obtido, erro, desvio e status são sempre calculados a
    partir das medidas, com precisão total (RN-08); o arredondamento acontece só no relatório.
    """

    REGRAS_DECISAO = ("FAIXA_GUARDA", "SIMPLES")

    def __init__(self, parametro: str, valor_nominal, tolerancia_superior, tolerancia_inferior,
                 medidas: Sequence[float], incerteza_expandida=None, regra_decisao: str = "FAIXA_GUARDA",
                 sequencia: int | None = None, status_gravado: str | None = None, id_: int | None = None):
        super().__init__(id_)
        self._parametro = _texto(parametro, "Parâmetro")
        self._nominal = _real(valor_nominal, f"{self._parametro}: valor nominal")
        self._tol_sup = _real(tolerancia_superior, f"{self._parametro}: tolerância superior")
        self._tol_inf = _real(tolerancia_inferior, f"{self._parametro}: tolerância inferior")
        if self._tol_inf > self._tol_sup:  # RN-09
            raise ErroValidacao(f"{self._parametro}: tolerância inferior ({self._tol_inf}) maior que a "
                                f"superior ({self._tol_sup})", "RN-09")
        medidas = [m for m in (medidas or []) if m is not None]
        if not 1 <= len(medidas) <= 2:
            raise ErroValidacao(f"{self._parametro}: informe 1 ou 2 medidas (recebido {len(medidas)})", "RN-01")
        self._medidas = tuple(_real(m, f"{self._parametro}: medida") for m in medidas)
        incerteza = _real(incerteza_expandida, "Incerteza expandida", obrigatorio=False)
        if incerteza is not None and incerteza < 0:
            raise ErroValidacao("Incerteza expandida não pode ser negativa")
        self._incerteza = incerteza
        if regra_decisao not in self.REGRAS_DECISAO:
            raise ErroValidacao(f"Regra de decisão desconhecida: {regra_decisao!r}")
        self._regra = regra_decisao
        if sequencia is not None and sequencia <= 0:
            raise ErroValidacao("Sequência deve ser maior que zero")
        self._sequencia = sequencia
        self._status_gravado = status_gravado
        self.calibracao: Calibracao | None = None  # preenchido por Calibracao.adicionar_medicao

    # --- somente leitura (RN-02) ---
    parametro = property(lambda self: self._parametro)
    valor_nominal = property(lambda self: self._nominal)
    tolerancia_superior = property(lambda self: self._tol_sup)
    tolerancia_inferior = property(lambda self: self._tol_inf)
    medidas = property(lambda self: self._medidas)
    incerteza_expandida = property(lambda self: self._incerteza)
    regra_decisao = property(lambda self: self._regra)
    sequencia = property(lambda self: self._sequencia)

    @property
    def medida_1(self) -> float:
        return self._medidas[0]

    @property
    def medida_2(self) -> float | None:
        return self._medidas[1] if len(self._medidas) > 1 else None

    # --- valores calculados ---
    @property
    def valor_obtido(self) -> float:
        """Média das medições (o certificado apresenta a média de duas medições)."""
        return statistics.fmean(self._medidas)

    @property
    def erro(self) -> float:
        return self.valor_obtido - self._nominal

    @property
    def desvio_padrao(self) -> float | None:
        return statistics.stdev(self._medidas) if len(self._medidas) > 1 else None

    @property
    def limite_superior(self) -> float:
        return self._nominal + self._tol_sup

    @property
    def limite_inferior(self) -> float:
        # a tolerância inferior já vem com sinal (-0,010 ou 0); por isso SOMA (achado A-03)
        return self._nominal + self._tol_inf

    def calcular_status(self) -> str:
        """Valida o resultado contra a tolerância.

        FAIXA_GUARDA (padrão, igual à aba Registro da planilha 06 com o sinal corrigido):
            REPROVADO se (obtido + U) > limite superior ou (obtido - U) < limite inferior.
        SIMPLES: compara só o valor obtido com os limites (U = 0), como na Aula 02.
        Nas duas regras existe uma margem de 1e-7 para o float não reprovar um valor
        que está exatamente no limite (ex.: 0,7 + 0,1 dá 0,7999999999999999).
        A regra definitiva depende da pendência P-03 com o responsável técnico.
        """
        u = (self._incerteza or 0.0) if self._regra == "FAIXA_GUARDA" else 0.0
        obtido = self.valor_obtido
        reprovado = ((obtido + u) > self.limite_superior + MARGEM_FLOAT
                     or (obtido - u) < self.limite_inferior - MARGEM_FLOAT)
        return "REPROVADO" if reprovado else "APROVADO"

    @property
    def status(self) -> str:
        return self.calcular_status()

    @property
    def aprovado(self) -> bool:
        return self.status == "APROVADO"

    @property
    def status_confere_com_banco(self) -> bool | None:
        """Ao reler do banco, confere se o status gravado é o mesmo que a classe calcula hoje."""
        return None if self._status_gravado is None else self._status_gravado == self.status

    def _definir_sequencia(self, sequencia: int) -> None:
        if self.persistido:
            raise ErroValidacao("RN-02: medição já gravada não pode ser renumerada", "RN-02")
        self._sequencia = sequencia

    def validar(self) -> None:
        if self._sequencia is None:
            raise ErroValidacao(f"{self._parametro}: medição sem sequência (adicione-a a uma Calibracao)")

    def __repr__(self) -> str:
        return (f"Medicao(seq={self._sequencia}, parametro={self._parametro!r}, "
                f"obtido={self.valor_obtido:.4f}, status={self.status})")


class Calibracao(EntidadeBase):
    """Cabeçalho do certificado: liga item, padrão, pessoas e as medições (composição).

    No banco das 3 tabelas, estes dados são repetidos em cada linha de `medicao`; a classe
    elimina essa repetição na memória e o repositório a mantém coerente na gravação.
    """

    def __init__(self, numero_certificado: str, item: ItemCalibrado, padrao: Padrao, tecnico: Tecnico,
                 data_medicao, solicitante: Solicitante | None = None, signatario: Signatario | None = None,
                 data_emissao=None, hora_medicao: str | None = None, plano_medicao: str | None = None,
                 fornecedor: str | None = None, incerteza_expandida=None, fator_k=None,
                 temperatura_ambiente=None, unidade: str = "mm", arquivo_origem: str | None = None,
                 hash_arquivo: str | None = None, regra_decisao: str = "FAIXA_GUARDA"):
        super().__init__(None)
        numero = _texto(numero_certificado, "Número do certificado")
        if not PADRAO_CERTIFICADO.fullmatch(numero):
            raise ErroValidacao(f"Número de certificado inválido: {numero!r} (formato 00091/2026)", "RN-23")
        self.numero_certificado = numero
        # Tipagem dos papéis: o banco tem triggers equivalentes (trg_medicao_papeis_*)
        for objeto, classe, rotulo in ((item, ItemCalibrado, "item calibrado"), (padrao, Padrao, "padrão"),
                                       (tecnico, Tecnico, "técnico executante")):
            if not isinstance(objeto, classe):
                raise ErroValidacao(f"O {rotulo} deve ser um objeto {classe.__name__} "
                                    f"(recebido {type(objeto).__name__})", "RN-13")
        if solicitante is not None and not isinstance(solicitante, Solicitante):
            raise ErroValidacao("O solicitante deve ser um objeto Solicitante", "RN-13")
        if signatario is not None and not isinstance(signatario, Signatario):
            raise ErroValidacao("O signatário deve ser um objeto Signatario (técnico não assina)", "RN-13")
        self.item, self.padrao, self.tecnico = item, padrao, tecnico
        self.solicitante, self.signatario = solicitante, signatario
        self.data_medicao = _data_iso(data_medicao, "Data da medição")
        self.data_emissao = _data_iso(data_emissao, "Data de emissão", obrigatorio=False)
        if self.data_emissao and self.data_emissao < self.data_medicao:
            raise ErroValidacao("Data de emissão anterior à data da medição")
        if hora_medicao is not None and not re.fullmatch(r"\d{2}:\d{2}:\d{2}", str(hora_medicao)):
            raise ErroValidacao(f"Hora da medição inválida: {hora_medicao!r} (use HH:MM:SS)")
        self.hora_medicao = hora_medicao
        self.plano_medicao = _texto(plano_medicao, "Plano de medição", obrigatorio=False)
        self.fornecedor = _texto(fornecedor, "Fornecedor", obrigatorio=False)
        self.incerteza_expandida = _real(incerteza_expandida, "Incerteza expandida", obrigatorio=False)
        self.fator_k = _real(fator_k, "Fator k", obrigatorio=False)
        if self.fator_k is not None and self.fator_k <= 0:
            raise ErroValidacao("Fator k deve ser maior que zero")
        temperatura = _real(temperatura_ambiente, "Temperatura ambiente", obrigatorio=False)
        if temperatura is not None and not 10 <= temperatura <= 30:
            raise ErroValidacao(f"Temperatura ambiente fora da faixa 10 a 30 °C: {temperatura}")
        self.temperatura_ambiente = temperatura
        if unidade not in UNIDADES:
            raise ErroValidacao(f"Unidade {unidade!r} fora do domínio aprovado", "RN-06")
        self.unidade = unidade
        self.arquivo_origem = arquivo_origem
        if hash_arquivo is not None and not re.fullmatch(r"[0-9a-f]{64}", hash_arquivo):
            raise ErroValidacao("hash_arquivo deve ser um SHA-256 com 64 caracteres hexadecimais")
        self.hash_arquivo = hash_arquivo
        self.regra_decisao = regra_decisao
        self._medicoes: list[Medicao] = []

    @property
    def medicoes(self) -> tuple[Medicao, ...]:
        return tuple(self._medicoes)

    def nova_medicao(self, parametro: str, valor_nominal, tolerancia_superior, tolerancia_inferior,
                     medidas: Sequence[float]) -> Medicao:
        """Fábrica: cria a medição já com a incerteza e a regra de decisão desta calibração."""
        medicao = Medicao(parametro, valor_nominal, tolerancia_superior, tolerancia_inferior, medidas,
                          incerteza_expandida=self.incerteza_expandida, regra_decisao=self.regra_decisao)
        return self.adicionar_medicao(medicao)

    def adicionar_medicao(self, medicao: Medicao) -> Medicao:
        if any(m.parametro == medicao.parametro for m in self._medicoes):  # RN-10
            raise ErroValidacao(f"Parâmetro {medicao.parametro!r} repetido no certificado "
                                f"{self.numero_certificado}", "RN-10")
        if medicao.sequencia is None:
            medicao._definir_sequencia(len(self._medicoes) + 1)
        medicao.calibracao = self
        self._medicoes.append(medicao)
        return medicao

    def validar(self) -> None:
        """Confere tudo o que o banco também confere, antes de abrir a transação."""
        if not self._medicoes:  # RN-01
            raise ErroValidacao(f"Certificado {self.numero_certificado} sem resultados de medição", "RN-01")
        self.item.validar()
        self.padrao.validar()
        self.padrao.validar_para_uso(self.data_medicao)  # RN-05
        self.tecnico.validar()
        if not self.tecnico.ativo:
            raise ErroValidacao(f"Técnico {self.tecnico.nome} está inativo e não pode executar calibração")
        for pessoa in (self.solicitante, self.signatario):
            if pessoa is not None:
                pessoa.validar()
        for medicao in self._medicoes:
            medicao.validar()

    @property
    def parecer_final(self) -> str:
        """Instrumento só é APROVADO se todos os parâmetros estiverem dentro da tolerância."""
        return "APROVADO" if self._medicoes and all(m.aprovado for m in self._medicoes) else "REPROVADO"

    def resumo(self) -> dict[str, int]:
        aprovados = sum(m.aprovado for m in self._medicoes)
        return {"parametros": len(self._medicoes), "aprovados": aprovados,
                "reprovados": len(self._medicoes) - aprovados}

    def __repr__(self) -> str:
        return f"Calibracao({self.numero_certificado!r}, item={self.item.codigo_tag!r}, medicoes={len(self._medicoes)})"


# ======================================================================
# 3. CONEXÃO E PERSISTÊNCIA (sqlite3)
# ======================================================================
class ConexaoSQLite:
    """Abre o banco, liga as chaves estrangeiras e controla transações.

    Uso:
        with ConexaoSQLite("schulz_mvp.db") as conexao:
            with conexao.transacao():
                ...
    """

    def __init__(self, caminho: str | Path, ddl: Path = ARQUIVO_DDL, recriar: bool = False):
        self.caminho = Path(caminho)
        self._ddl = Path(ddl)
        self._recriar = recriar
        self._con: sqlite3.Connection | None = None
        self._nivel_transacao = 0

    # --- ciclo de vida (gerenciador de contexto) ---
    def abrir(self) -> "ConexaoSQLite":
        if self._recriar and self.caminho.exists():
            try:
                self.caminho.unlink()
            except OSError as erro:  # no Windows acontece quando o .db está aberto em outro programa
                raise ErroPersistencia(f"Não foi possível recriar {self.caminho.name}: feche o arquivo no "
                                       f"SQLite Viewer (ou em outro programa) e rode de novo ({erro})") from erro
        try:
            self._con = sqlite3.connect(self.caminho)
            self._con.row_factory = sqlite3.Row
            self._con.execute("PRAGMA foreign_keys = ON")  # o SQLite vem com FK desligada
            if self._con.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ErroPersistencia("Não foi possível ligar as chaves estrangeiras (PRAGMA foreign_keys)")
            if not self.listar_tabelas():
                self._con.executescript(self._ddl.read_text(encoding="utf-8"))
                self._con.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as erro:
            raise traduzir_erro_sqlite(erro, f"Abrir o banco {self.caminho.name}") from erro
        return self

    def fechar(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> "ConexaoSQLite":
        return self.abrir()

    def __exit__(self, tipo, valor, rastro) -> None:
        if self._con is not None and tipo is not None and self._nivel_transacao == 0:
            self._con.rollback()
        self.fechar()

    @property
    def aberta(self) -> bool:
        return self._con is not None

    def _conexao(self) -> sqlite3.Connection:
        if self._con is None:
            raise ErroPersistencia("Conexão fechada: use 'with ConexaoSQLite(...)' ou chame abrir()")
        return self._con

    # --- transação (RN-29: falha parcial desfaz a carga) ---
    @contextmanager
    def transacao(self) -> Iterator["ConexaoSQLite"]:
        """Agrupa comandos: tudo é gravado (COMMIT) ou nada é gravado (ROLLBACK).

        Transações aninhadas são absorvidas pela mais externa, para que um repositório possa
        chamar outro sem confirmar a gravação pela metade.
        """
        con = self._conexao()
        self._nivel_transacao += 1
        try:
            yield self
        except BaseException:
            self._nivel_transacao -= 1
            if self._nivel_transacao == 0:
                con.rollback()
            raise
        else:
            self._nivel_transacao -= 1
            if self._nivel_transacao == 0:
                con.commit()

    # --- execução de SQL com tradução de erros ---
    def executar(self, sql: str, parametros: Sequence | dict = (), contexto: str = "Gravar no banco") -> sqlite3.Cursor:
        """INSERT/UPDATE/DELETE com parâmetros (?), nunca concatenando valores no SQL."""
        try:
            cursor = self._conexao().execute(sql, parametros)
        except sqlite3.Error as erro:
            if self._nivel_transacao == 0:
                self._conexao().rollback()
            raise traduzir_erro_sqlite(erro, contexto) from erro
        if self._nivel_transacao == 0:
            self._conexao().commit()
        return cursor

    def consultar(self, sql: str, parametros: Sequence | dict = (), contexto: str = "Consultar o banco") -> list[sqlite3.Row]:
        try:
            return self._conexao().execute(sql, parametros).fetchall()
        except sqlite3.Error as erro:
            raise traduzir_erro_sqlite(erro, contexto) from erro

    def listar_tabelas(self) -> list[str]:
        linhas = self._conexao().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [linha[0] for linha in linhas]


class RepositorioBase(ABC):
    """Base dos repositórios (padrão DAO): cada um conversa com uma tabela."""

    TABELA: str = ""

    def __init__(self, conexao: ConexaoSQLite):
        self._conexao = conexao

    def contar(self) -> int:
        return self._conexao.consultar(f"SELECT COUNT(*) AS n FROM {self.TABELA}")[0]["n"]


class TecnicoRepositorio(RepositorioBase):
    """Tabela tecnico_solicitante: grava e lê Tecnico, Signatario e Solicitante."""

    TABELA = "tecnico_solicitante"
    _CLASSES = {"TECNICO": Tecnico, "SIGNATARIO": Signatario, "SOLICITANTE": Solicitante}

    @classmethod
    def _montar(cls, linha: sqlite3.Row, prefixo: str = "") -> Pessoa:
        """Fábrica polimórfica: a coluna `tipo` decide qual subclasse instanciar."""
        classe = cls._CLASSES[linha[f"{prefixo}tipo"]]
        return classe(nome=linha[f"{prefixo}nome"], setor=linha[f"{prefixo}setor"],
                      matricula=linha[f"{prefixo}matricula"], nome_abreviado=linha[f"{prefixo}nome_abreviado"],
                      ativo=bool(linha[f"{prefixo}ativo"]), id_=linha[f"{prefixo}id_pessoa"])

    def inserir(self, pessoa: Pessoa) -> int:
        pessoa.validar()
        if pessoa.persistido:
            raise ErroValidacao(f"{pessoa!r} já está gravado; use atualizar()")
        cursor = self._conexao.executar(
            "INSERT INTO tecnico_solicitante (nome, nome_abreviado, matricula, tipo, setor, ativo) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pessoa.nome, pessoa.nome_abreviado, pessoa.matricula, pessoa.tipo, pessoa.setor, int(pessoa.ativo)),
            contexto=f"Cadastrar {pessoa.papel().lower()} '{pessoa.nome}'")
        pessoa._vincular_id(cursor.lastrowid)
        return pessoa.id

    def atualizar(self, pessoa: Pessoa) -> None:
        pessoa.validar()
        if not pessoa.persistido:
            raise ErroValidacao(f"{pessoa!r} ainda não foi gravado; use inserir()")
        cursor = self._conexao.executar(
            "UPDATE tecnico_solicitante SET nome = ?, nome_abreviado = ?, matricula = ?, setor = ?, ativo = ? "
            "WHERE id_pessoa = ? AND tipo = ?",
            (pessoa.nome, pessoa.nome_abreviado, pessoa.matricula, pessoa.setor, int(pessoa.ativo),
             pessoa.id, pessoa.tipo),
            contexto=f"Atualizar cadastro de '{pessoa.nome}'")
        if cursor.rowcount == 0:
            raise RegistroNaoEncontrado(f"Cadastro id={pessoa.id} ({pessoa.tipo}) não encontrado")

    def excluir(self, pessoa: Pessoa) -> None:
        """DELETE. Só funciona para cadastro sem medição ligada; com histórico a FK bloqueia (RN-16)."""
        if not pessoa.persistido:
            raise ErroValidacao(f"{pessoa!r} ainda não foi gravado")
        try:
            cursor = self._conexao.executar("DELETE FROM tecnico_solicitante WHERE id_pessoa = ?", (pessoa.id,),
                                            contexto=f"Excluir '{pessoa.nome}'")
        except ReferenciaInvalida as erro:
            raise ReferenciaInvalida(f"'{pessoa.nome}' tem medições ou equipamentos ligados e não pode ser "
                                     f"excluído; use desativar()", "RN-16", erro.original) from erro
        if cursor.rowcount == 0:
            raise RegistroNaoEncontrado(f"Cadastro id={pessoa.id} não encontrado")

    def desativar(self, pessoa: Pessoa) -> None:
        """Exclusão lógica: o cadastro some das listas de uso, mas o histórico continua (RN-16)."""
        pessoa.ativo = False
        self.atualizar(pessoa)

    def buscar_por_id(self, id_pessoa: int) -> Pessoa:
        linhas = self._conexao.consultar("SELECT * FROM tecnico_solicitante WHERE id_pessoa = ?", (id_pessoa,))
        if not linhas:
            raise RegistroNaoEncontrado(f"Pessoa id={id_pessoa} não cadastrada")
        return self._montar(linhas[0])

    def buscar_por_matricula(self, matricula: str) -> Tecnico | None:
        linhas = self._conexao.consultar("SELECT * FROM tecnico_solicitante WHERE matricula = ?", (matricula,))
        return self._montar(linhas[0]) if linhas else None

    def buscar_por_nome(self, nome: str, tipo: str) -> Pessoa | None:
        """Busca tolerante a acento e a nome abreviado (A-06)."""
        for linha in self._conexao.consultar("SELECT * FROM tecnico_solicitante WHERE tipo = ?", (tipo,)):
            pessoa = self._montar(linha)
            if pessoa.reconhece_nome(nome):
                return pessoa
        return None

    def listar(self, tipo: str | None = None) -> list[Pessoa]:
        if tipo:
            linhas = self._conexao.consultar(
                "SELECT * FROM tecnico_solicitante WHERE tipo = ? ORDER BY nome", (tipo,))
        else:
            linhas = self._conexao.consultar("SELECT * FROM tecnico_solicitante ORDER BY tipo, nome")
        return [self._montar(linha) for linha in linhas]

    def obter_ou_inserir(self, pessoa: Pessoa) -> Pessoa:
        existente = self.buscar_por_nome(pessoa.nome, pessoa.tipo)
        if existente:
            return existente
        self.inserir(pessoa)
        return pessoa


class EquipamentoRepositorio(RepositorioBase):
    """Tabela equipamento: grava e lê ItemCalibrado e Padrao."""

    TABELA = "equipamento"
    _CLASSES = {"ITEM_CALIBRADO": ItemCalibrado, "PADRAO": Padrao}

    @classmethod
    def _montar(cls, linha: sqlite3.Row, prefixo: str = "", solicitante: Solicitante | None = None) -> Equipamento:
        classe = cls._CLASSES[linha[f"{prefixo}categoria"]]
        dados = dict(
            codigo_tag=linha[f"{prefixo}codigo"], descricao=linha[f"{prefixo}denominacao"],
            fabricante=linha[f"{prefixo}fabricante"], modelo=linha[f"{prefixo}modelo"],
            numero_serie=linha[f"{prefixo}numero_serie"], unidade=linha[f"{prefixo}unidade"],
            resolucao=linha[f"{prefixo}resolucao"], certificado_calibracao=linha[f"{prefixo}certificado_calibracao"],
            data_ultima_calibracao=linha[f"{prefixo}data_ultima_calibracao"],
            data_proxima_calibracao=linha[f"{prefixo}data_proxima_calibracao"],
            periodicidade_meses=linha[f"{prefixo}periodicidade_meses"], ativo=bool(linha[f"{prefixo}ativo"]),
            id_=linha[f"{prefixo}id_equipamento"])
        if classe is ItemCalibrado:
            dados["solicitante"] = solicitante
        return classe(**dados)

    @staticmethod
    def _valores(e: Equipamento) -> tuple:
        solicitante = getattr(e, "solicitante", None)
        if solicitante is not None and not solicitante.persistido:
            raise ErroValidacao(f"Grave o solicitante '{solicitante.nome}' antes do equipamento", "RN-22")
        return (e.codigo_tag, e.descricao, e.CATEGORIA, e.fabricante, e.modelo, e.numero_serie, e.unidade,
                e.resolucao, e.certificado_calibracao, e.data_ultima_calibracao, e.data_proxima_calibracao,
                e.periodicidade_meses, solicitante.id if solicitante else None, int(e.ativo))

    def inserir(self, equipamento: Equipamento) -> int:
        equipamento.validar()
        if equipamento.persistido:
            raise ErroValidacao(f"{equipamento!r} já está gravado; use atualizar()")
        cursor = self._conexao.executar(
            "INSERT INTO equipamento (codigo, denominacao, categoria, fabricante, modelo, numero_serie, unidade, "
            "resolucao, certificado_calibracao, data_ultima_calibracao, data_proxima_calibracao, "
            "periodicidade_meses, id_solicitante, ativo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            self._valores(equipamento), contexto=f"Cadastrar equipamento {equipamento.codigo_tag}")
        equipamento._vincular_id(cursor.lastrowid)
        return equipamento.id

    def atualizar(self, equipamento: Equipamento) -> None:
        equipamento.validar()
        if not equipamento.persistido:
            raise ErroValidacao(f"{equipamento!r} ainda não foi gravado; use inserir()")
        cursor = self._conexao.executar(
            "UPDATE equipamento SET codigo = ?, denominacao = ?, categoria = ?, fabricante = ?, modelo = ?, "
            "numero_serie = ?, unidade = ?, resolucao = ?, certificado_calibracao = ?, data_ultima_calibracao = ?, "
            "data_proxima_calibracao = ?, periodicidade_meses = ?, id_solicitante = ?, ativo = ? "
            "WHERE id_equipamento = ?",
            (*self._valores(equipamento), equipamento.id),
            contexto=f"Atualizar equipamento {equipamento.codigo_tag}")
        if cursor.rowcount == 0:
            raise RegistroNaoEncontrado(f"Equipamento id={equipamento.id} não encontrado")

    def excluir(self, equipamento: Equipamento) -> None:
        """DELETE. Equipamento que já aparece em alguma medição não pode ser apagado (RN-16)."""
        if not equipamento.persistido:
            raise ErroValidacao(f"{equipamento!r} ainda não foi gravado")
        try:
            cursor = self._conexao.executar("DELETE FROM equipamento WHERE id_equipamento = ?", (equipamento.id,),
                                            contexto=f"Excluir equipamento {equipamento.codigo_tag}")
        except ReferenciaInvalida as erro:
            raise ReferenciaInvalida(f"Equipamento {equipamento.codigo_tag} tem medições ligadas e não pode ser "
                                     f"excluído; use desativar()", "RN-16", erro.original) from erro
        if cursor.rowcount == 0:
            raise RegistroNaoEncontrado(f"Equipamento id={equipamento.id} não encontrado")

    def desativar(self, equipamento: Equipamento) -> None:
        equipamento.ativo = False
        self.atualizar(equipamento)

    def _solicitante_de(self, linha: sqlite3.Row) -> Solicitante | None:
        if linha["id_solicitante"] is None:
            return None
        return TecnicoRepositorio(self._conexao).buscar_por_id(linha["id_solicitante"])

    def buscar_por_id(self, id_equipamento: int) -> Equipamento:
        linhas = self._conexao.consultar("SELECT * FROM equipamento WHERE id_equipamento = ?", (id_equipamento,))
        if not linhas:
            raise RegistroNaoEncontrado(f"Equipamento id={id_equipamento} não cadastrado")
        return self._montar(linhas[0], solicitante=self._solicitante_de(linhas[0]))

    def buscar_por_codigo(self, codigo_tag: str) -> Equipamento | None:
        linhas = self._conexao.consultar("SELECT * FROM equipamento WHERE codigo = ?", (codigo_tag,))
        return self._montar(linhas[0], solicitante=self._solicitante_de(linhas[0])) if linhas else None

    def listar(self, categoria: str | None = None) -> list[Equipamento]:
        sql, par = "SELECT * FROM equipamento", ()
        if categoria:
            sql, par = sql + " WHERE categoria = ?", (categoria,)
        return [self._montar(l, solicitante=self._solicitante_de(l))
                for l in self._conexao.consultar(sql + " ORDER BY codigo", par)]

    def padroes_vencidos(self, data_referencia) -> list[Padrao]:
        """Padrões sem validade ou com a próxima calibração anterior à data informada."""
        data_iso = _data_iso(data_referencia, "Data de referência")
        linhas = self._conexao.consultar(
            "SELECT * FROM equipamento WHERE categoria = 'PADRAO' "
            "AND (data_proxima_calibracao IS NULL OR data_proxima_calibracao < ?) ORDER BY codigo", (data_iso,))
        return [self._montar(linha) for linha in linhas]


class MedicaoRepositorio(RepositorioBase):
    """Tabela medicao: grava uma Calibracao inteira e a reconstrói com JOIN."""

    TABELA = "medicao"

    # Consulta do certificado: medicao + item + padrão + técnico + solicitante + signatário
    SQL_CERTIFICADO = """
        SELECT m.*,
               e.id_equipamento AS e_id_equipamento, e.codigo AS e_codigo, e.denominacao AS e_denominacao,
               e.categoria AS e_categoria, e.fabricante AS e_fabricante, e.modelo AS e_modelo,
               e.numero_serie AS e_numero_serie, e.unidade AS e_unidade, e.resolucao AS e_resolucao,
               e.certificado_calibracao AS e_certificado_calibracao,
               e.data_ultima_calibracao AS e_data_ultima_calibracao,
               e.data_proxima_calibracao AS e_data_proxima_calibracao,
               e.periodicidade_meses AS e_periodicidade_meses, e.ativo AS e_ativo,
               p.id_equipamento AS p_id_equipamento, p.codigo AS p_codigo, p.denominacao AS p_denominacao,
               p.categoria AS p_categoria, p.fabricante AS p_fabricante, p.modelo AS p_modelo,
               p.numero_serie AS p_numero_serie, p.unidade AS p_unidade, p.resolucao AS p_resolucao,
               p.certificado_calibracao AS p_certificado_calibracao,
               p.data_ultima_calibracao AS p_data_ultima_calibracao,
               p.data_proxima_calibracao AS p_data_proxima_calibracao,
               p.periodicidade_meses AS p_periodicidade_meses, p.ativo AS p_ativo,
               t.id_pessoa AS t_id_pessoa, t.nome AS t_nome, t.nome_abreviado AS t_nome_abreviado,
               t.matricula AS t_matricula, t.tipo AS t_tipo, t.setor AS t_setor, t.ativo AS t_ativo,
               s.id_pessoa AS s_id_pessoa, s.nome AS s_nome, s.nome_abreviado AS s_nome_abreviado,
               s.matricula AS s_matricula, s.tipo AS s_tipo, s.setor AS s_setor, s.ativo AS s_ativo,
               g.id_pessoa AS g_id_pessoa, g.nome AS g_nome, g.nome_abreviado AS g_nome_abreviado,
               g.matricula AS g_matricula, g.tipo AS g_tipo, g.setor AS g_setor, g.ativo AS g_ativo
        FROM medicao m
        JOIN equipamento              e ON e.id_equipamento = m.id_equipamento
        JOIN equipamento              p ON p.id_equipamento = m.id_padrao
        JOIN tecnico_solicitante      t ON t.id_pessoa      = m.id_tecnico
        LEFT JOIN tecnico_solicitante s ON s.id_pessoa      = m.id_solicitante
        LEFT JOIN tecnico_solicitante g ON g.id_pessoa      = m.id_signatario
        WHERE m.numero_certificado = ?
        ORDER BY m.sequencia
    """

    def certificado_existe(self, numero_certificado: str) -> int:
        linhas = self._conexao.consultar(
            "SELECT COUNT(*) AS n FROM medicao WHERE numero_certificado = ?", (numero_certificado,))
        return linhas[0]["n"]

    def inserir_calibracao(self, calibracao: Calibracao) -> list[int]:
        """Grava todas as medições do certificado em UMA transação (RN-29)."""
        calibracao.validar()
        ja = self.certificado_existe(calibracao.numero_certificado)
        if ja:  # RN-10 / RN-23: verifica antes da carga e não sobrescreve
            raise RegistroDuplicado(f"Certificado {calibracao.numero_certificado} já importado "
                                    f"({ja} parâmetros); nada foi alterado", "RN-10")
        for envolvido in (calibracao.item, calibracao.padrao, calibracao.tecnico,
                          calibracao.solicitante, calibracao.signatario):
            if envolvido is not None and not envolvido.persistido:
                raise ErroValidacao(f"{envolvido!r} precisa ser cadastrado antes da calibração", "RN-22")
        c = calibracao
        ids: list[int] = []
        with self._conexao.transacao():
            for m in c.medicoes:
                cursor = self._conexao.executar(
                    """INSERT INTO medicao (
                        numero_certificado, id_equipamento, id_padrao, id_tecnico, id_solicitante, id_signatario,
                        plano_medicao, fornecedor, data_medicao, hora_medicao, data_emissao, sequencia, parametro,
                        valor_nominal, tolerancia_superior, tolerancia_inferior, medida_1, medida_2, valor_obtido,
                        erro, desvio_padrao, incerteza_expandida, fator_k, temperatura_ambiente, unidade, status,
                        arquivo_origem, hash_arquivo)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (c.numero_certificado, c.item.id, c.padrao.id, c.tecnico.id,
                     c.solicitante.id if c.solicitante else None, c.signatario.id if c.signatario else None,
                     c.plano_medicao, c.fornecedor, c.data_medicao, c.hora_medicao, c.data_emissao,
                     m.sequencia, m.parametro, m.valor_nominal, m.tolerancia_superior, m.tolerancia_inferior,
                     m.medida_1, m.medida_2, m.valor_obtido, m.erro, m.desvio_padrao, m.incerteza_expandida,
                     c.fator_k, c.temperatura_ambiente, c.unidade, m.status, c.arquivo_origem, c.hash_arquivo),
                    contexto=f"Gravar '{m.parametro}' do certificado {c.numero_certificado}")
                ids.append(cursor.lastrowid)
            for m, novo_id in zip(c.medicoes, ids):
                m._vincular_id(novo_id)
        return ids

    def buscar_calibracao(self, numero_certificado: str) -> Calibracao:
        """SELECT com JOIN nas 3 tabelas e reconstrução dos objetos (mapeamento objeto-relacional)."""
        linhas = self._conexao.consultar(self.SQL_CERTIFICADO, (numero_certificado,),
                                         contexto=f"Consultar certificado {numero_certificado}")
        if not linhas:
            raise RegistroNaoEncontrado(f"Certificado {numero_certificado} não encontrado")
        l0 = linhas[0]
        solicitante = TecnicoRepositorio._montar(l0, "s_") if l0["s_id_pessoa"] is not None else None
        signatario = TecnicoRepositorio._montar(l0, "g_") if l0["g_id_pessoa"] is not None else None
        calibracao = Calibracao(
            l0["numero_certificado"],
            item=EquipamentoRepositorio._montar(l0, "e_", solicitante=solicitante),
            padrao=EquipamentoRepositorio._montar(l0, "p_"),
            tecnico=TecnicoRepositorio._montar(l0, "t_"),
            data_medicao=l0["data_medicao"], solicitante=solicitante, signatario=signatario,
            data_emissao=l0["data_emissao"], hora_medicao=l0["hora_medicao"], plano_medicao=l0["plano_medicao"],
            fornecedor=l0["fornecedor"], incerteza_expandida=l0["incerteza_expandida"], fator_k=l0["fator_k"],
            temperatura_ambiente=l0["temperatura_ambiente"], unidade=l0["unidade"],
            arquivo_origem=l0["arquivo_origem"], hash_arquivo=l0["hash_arquivo"])
        for linha in linhas:
            calibracao.adicionar_medicao(Medicao(
                linha["parametro"], linha["valor_nominal"], linha["tolerancia_superior"],
                linha["tolerancia_inferior"], [linha["medida_1"], linha["medida_2"]],
                incerteza_expandida=linha["incerteza_expandida"], sequencia=linha["sequencia"],
                status_gravado=linha["status"], id_=linha["id_medicao"]))
        return calibracao

    def registrar_emissao(self, numero_certificado: str, data_emissao, signatario: Signatario) -> int:
        """UPDATE permitido: dados administrativos da emissão (os valores medidos são bloqueados)."""
        if not isinstance(signatario, Signatario) or not signatario.persistido:
            raise ErroValidacao("Informe um signatário cadastrado", "RN-13")
        data_iso = _data_iso(data_emissao, "Data de emissão")
        cursor = self._conexao.executar(
            "UPDATE medicao SET data_emissao = ?, id_signatario = ? "
            "WHERE numero_certificado = ? AND data_medicao <= ?",
            (data_iso, signatario.id, numero_certificado, data_iso),
            contexto=f"Registrar emissão do certificado {numero_certificado}")
        if cursor.rowcount == 0:
            raise RegistroNaoEncontrado(f"Certificado {numero_certificado} não encontrado "
                                        f"ou emissão anterior à medição")
        return cursor.rowcount

    # --- consultas relacionais (JOIN) para relatórios ---
    def historico_equipamento(self, codigo_tag: str) -> list[sqlite3.Row]:
        return self._conexao.consultar(
            """SELECT m.numero_certificado, m.data_medicao, p.codigo AS padrao, t.nome AS tecnico,
                      COUNT(*) AS parametros, SUM(m.status = 'APROVADO') AS aprovados,
                      SUM(m.status = 'REPROVADO') AS reprovados
               FROM medicao m
               JOIN equipamento e         ON e.id_equipamento = m.id_equipamento
               JOIN equipamento p         ON p.id_equipamento = m.id_padrao
               JOIN tecnico_solicitante t ON t.id_pessoa      = m.id_tecnico
               WHERE e.codigo = ?
               GROUP BY m.numero_certificado, m.data_medicao, p.codigo, t.nome
               ORDER BY m.data_medicao DESC""", (codigo_tag,))

    def resumo_por_tecnico(self) -> list[sqlite3.Row]:
        return self._conexao.consultar(
            """SELECT t.nome AS tecnico, t.matricula,
                      COUNT(DISTINCT m.numero_certificado) AS certificados,
                      COUNT(m.id_medicao) AS parametros,
                      COALESCE(SUM(m.status = 'REPROVADO'), 0) AS reprovados
               FROM tecnico_solicitante t
               LEFT JOIN medicao m ON m.id_tecnico = t.id_pessoa
               WHERE t.tipo = 'TECNICO'
               GROUP BY t.id_pessoa
               ORDER BY parametros DESC, t.nome""")

    def uso_dos_padroes(self) -> list[sqlite3.Row]:
        return self._conexao.consultar(
            """SELECT p.codigo, p.denominacao, p.data_proxima_calibracao,
                      COUNT(m.id_medicao) AS medicoes, MAX(m.data_medicao) AS ultima_medicao
               FROM equipamento p
               LEFT JOIN medicao m ON m.id_padrao = p.id_equipamento
               WHERE p.categoria = 'PADRAO'
               GROUP BY p.id_equipamento
               ORDER BY medicoes DESC, p.codigo""")


# ======================================================================
# Apresentação: relatório de calibração no terminal
# ======================================================================
class RelatorioCalibracao:
    """Monta o texto do certificado a partir de um objeto Calibracao (sem acessar o banco)."""

    LARGURA = 96

    def __init__(self, calibracao: Calibracao):
        self.c = calibracao

    def _linha(self, caractere: str = "-") -> str:
        return caractere * self.LARGURA

    def texto(self) -> str:
        c, larg = self.c, self.LARGURA
        r = c.resumo()
        tit = f"CERTIFICADO DE CALIBRAÇÃO Nº {c.numero_certificado}"
        data = f"Data: {_data_br(c.data_emissao)}"
        partes = [
            self._linha("="),
            tit + data.rjust(larg - len(tit)),
            f"Solicitante: {c.solicitante.nome if c.solicitante else '-'}",
            self._linha(),
            "1. Descrição do instrumento calibrado",
            f"   Denominação: {c.item.descricao}",
            f"   Código:      {c.item.codigo_tag}",
            f"   Fabricante:  {c.item.fabricante_exibicao()}",
            "",
            "2. Padrão utilizado",
            *("   " + linha for linha in c.padrao.texto_certificado().splitlines()),
            "",
            "3. Condições da calibração",
            f"   Medição em {_data_br(c.data_medicao)} {c.hora_medicao or ''} | plano {c.plano_medicao or '-'} | "
            f"temperatura {_num_br(c.temperatura_ambiente, 1)} °C",
            f"   Resultado apresentado como a média de {max(len(m.medidas) for m in c.medicoes)} medições.",
            "",
            f"4. Resultados ({c.unidade})",
        ]
        cab = (f"   {'Nº':>2}  {'Parâmetro avaliado':<20}{'Nominal':>9}{'Tol. +':>8}{'Tol. -':>8}"
               f"{'Obtido':>9}{'Erro':>8}  {'Status*':<10}")
        partes += [cab, "   " + "-" * (len(cab) - 3)]
        for m in c.medicoes:
            partes.append(
                f"   {m.sequencia:>2}  {m.parametro:<20}{_num_br(m.valor_nominal):>9}"
                f"{_num_br(m.tolerancia_superior):>8}{_num_br(m.tolerancia_inferior):>8}"
                f"{_num_br(m.valor_obtido):>9}{_num_br(m.erro):>8}  {m.status:<10}")
        partes += [
            "",
            f"5. Incerteza de medição: {_num_br(c.incerteza_expandida)} {c.unidade}    k = {_num_br(c.fator_k, 2)}",
            "",
            f"{c.tecnico.papel()}: {c.tecnico.nome}" + (f" (matrícula {c.tecnico.matricula})" if c.tecnico.matricula else ""),
            f"{c.signatario.papel()}: {c.signatario.nome}" if c.signatario else "Signatário autorizado: (pendente)",
            self._linha(),
            f"Resumo: {r['parametros']} parâmetros | {r['aprovados']} aprovados | {r['reprovados']} reprovados",
            f"PARECER FINAL*: INSTRUMENTO {c.parecer_final}",
            f"* Status informativo ({'faixa de guarda' if c.regra_decisao == 'FAIXA_GUARDA' else 'regra simples'}, U = "
            f"{_num_br(c.incerteza_expandida)} {c.unidade}); não consta do certificado oficial (P-03).",
            f"Rastreabilidade: {c.arquivo_origem or '-'} | SHA-256 {(c.hash_arquivo or '-')[:16]}...",
            self._linha("="),
        ]
        return "\n".join(partes)

    def imprimir(self) -> None:
        print(self.texto())
