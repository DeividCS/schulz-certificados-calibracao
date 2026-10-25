"""
Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A.
Fase 03 - MVP (Parte 1): classe principal de demonstração.

A classe Principal roda as etapas abaixo, nessa ordem:
  1. conecta no SQLite e cria as 3 tabelas (03_banco.sql da Fase 02);
  2. cria os objetos com os dados reais da Schulz e faz o INSERT dos cadastros;
  3. faz UPDATE e DELETE de cadastros (inclusive um registro de teste);
  4. lê o Report do Calypso, monta a Calibracao 00091/2026 com 14 objetos Medicao,
     calcula o status de cada parâmetro e grava tudo numa transação só;
  5. roda as consultas com JOIN;
  6. imprime o relatório de calibração, montado de novo a partir do banco;
  7. tenta gravar dados errados de propósito, para mostrar o tratamento de erros.

Como rodar (no Windows, se "python" não funcionar, use "py"):
    pip install -r requirements.txt
    python 05_main.py                          # demonstração completa (recria o schulz_mvp.db)
    python 05_main.py --relatorio 00091/2026   # só mostra o relatório de um banco já gravado
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import extrator  # Fase 02: leitura do Calypso e das planilhas (reaproveitado)

# o arquivo se chama 04_classes.py e nome que começa com número não aceita "import" normal
poo = importlib.import_module("04_classes")

BASE = Path(__file__).resolve().parent
DADOS = BASE / "dados"

for _fluxo in (sys.stdout, sys.stderr):  # evita erro de acento no console do Windows
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(errors="replace")


def titulo(texto: str) -> None:
    print(f"\n{'=' * 96}\n{texto}\n{'=' * 96}")


def tabela(linhas, colunas: list[str]) -> None:
    """Imprime o resultado de uma consulta com as colunas alinhadas."""
    dados = [[("-" if l[c] is None else str(l[c])) for c in colunas] for l in linhas]
    larg = [max(len(c), *(len(d[i]) for d in dados)) if dados else len(c) for i, c in enumerate(colunas)]
    print("   " + "  ".join(c.ljust(w) for c, w in zip(colunas, larg)))
    print("   " + "  ".join("-" * w for w in larg))
    for d in dados:
        print("   " + "  ".join(v.ljust(w) for v, w in zip(d, larg)))


def tentar(descricao: str, acao) -> bool:
    """Roda uma ação que TEM que dar erro e mostra a mensagem. Retorna True se foi recusada."""
    try:
        acao()
    except poo.ErroValidacao as erro:
        print(f"   [RECUSADO pela classe     ] {descricao}\n      -> {erro}")
        return True
    except poo.ErroPersistencia as erro:
        origem = "pelo banco      " if erro.original else "pelo repositório"
        print(f"   [RECUSADO {origem}] {descricao}\n      -> {type(erro).__name__}: {erro}")
        if erro.original:
            print(f"      (mensagem original do SQLite: {erro.original})")
        return True
    print(f"   [FALHA] {descricao}: foi aceito, mas deveria ter sido recusado")
    return False


class Principal:
    """Classe principal do MVP: amarra as classes do 04_classes.py com os dados da Schulz."""

    def __init__(self, caminho_banco: str | Path, arquivo_complemento: str | Path):
        self.caminho_banco = Path(caminho_banco)
        self.comp = json.loads(Path(arquivo_complemento).read_text(encoding="utf-8"))
        self.arquivo_calypso = DADOS / "04__Registro_MMC.xlsx"
        self.report = extrator.ler_report(self.arquivo_calypso)
        self.conexao: poo.ConexaoSQLite | None = None
        # objetos que passam de uma etapa para outra
        self.pessoas = self.equipamentos = self.medicoes = None
        self.item = self.signatario = self.solicitante = self.tecnico = self.calibracao = None

    # ------------------------------------------------------------------
    def executar(self) -> int:
        titulo("1) Conexão com o SQLite")
        # demonstração: o banco é recriado a cada execução para o resultado ser sempre o mesmo
        with poo.ConexaoSQLite(self.caminho_banco, recriar=True) as conexao:
            self.conexao = conexao
            self.pessoas = poo.TecnicoRepositorio(conexao)
            self.equipamentos = poo.EquipamentoRepositorio(conexao)
            self.medicoes = poo.MedicaoRepositorio(conexao)
            print(f"   Banco: {self.caminho_banco.name} | chaves estrangeiras ligadas | "
                  f"tabelas: {conexao.listar_tabelas()}")

            self.cadastrar()
            self.atualizar_e_excluir_cadastros()
            self.importar_calibracao()
            self.consultas()
            confere = self.relatorio()
            falhas = self.cenarios_de_erro()

        titulo("Fim da demonstração")
        print(f"   Banco gerado: {self.caminho_banco.name} (dá para abrir no VS Code com a extensão SQLite Viewer)")
        return 0 if falhas == 0 and confere else 1

    # ------------------------------------------------------------------
    def cadastrar(self) -> None:
        """Etapa 2: cria os objetos e faz o INSERT de técnicos, áreas, signatário e equipamentos."""
        titulo("2) Instanciação dos objetos e INSERT dos cadastros")
        comp = self.comp
        cadastros = extrator.ler_banco_de_dados(DADOS / "03__Planilha_MMC.xlsm")
        padroes_csv = extrator.ler_padroes_csv(DADOS / "padroes.csv")

        with self.conexao.transacao():  # ou grava o cadastro inteiro ou não grava nada (RN-29)
            # metrologistas da aba BANCO DE DADOS, com o nome do jeito que aparece no Calypso
            for nome in cadastros["metrologistas"]:
                self.pessoas.inserir(poo.Tecnico(nome, setor="Metrologia", nome_abreviado=nome))
            for area in cadastros["areas"]:
                self.pessoas.inserir(poo.Solicitante(area, setor=area))
            self.signatario = poo.Signatario(comp["signatario"], setor="Metrologia")
            self.pessoas.inserir(self.signatario)

            # achado A-07: 'Usinagem I' não está na lista de áreas da planilha, então é cadastrada com aviso
            self.solicitante = self.pessoas.buscar_por_nome(comp["solicitante"], "SOLICITANTE")
            if self.solicitante is None:
                self.solicitante = poo.Solicitante(comp["solicitante"], setor=comp["solicitante"])
                self.pessoas.inserir(self.solicitante)
                print(f"   Aviso A-07: a área '{self.solicitante.nome}' não estava na lista oficial; "
                      f"foi cadastrada (pendência P-04)")

            # os 8 padrões da aba BANCO DE DADOS; só o 500.011 tem certificado e validade (padroes.csv)
            dados_csv = {extrator.codigo_padrao(p["codigo"]): p for p in padroes_csv}
            for codigo in cadastros["padroes"]:
                p = dados_csv.get(codigo)
                self.equipamentos.inserir(poo.Padrao(
                    codigo, p["denominacao"] if p else "Máquina de medição por coordenadas",
                    fabricante=p and p["fabricante"], modelo=p and p["modelo"],
                    numero_serie=p and p["numero_serie"], unidade=(p and p["unidade"]) or "mm",
                    resolucao=p and p["resolucao"], certificado_calibracao=p and p["certificado_calibracao"],
                    data_ultima_calibracao=p and p["data_ultima_calibracao"],
                    data_proxima_calibracao=p and p["data_proxima_calibracao"]))

            self.item = poo.ItemCalibrado(comp["codigo_item"], comp["denominacao"], fabricante=comp["fabricante"],
                                          modelo=comp["modelo"], numero_serie=comp["numero_serie"],
                                          unidade=comp["unidade"], resolucao=comp["resolucao"])
            self.equipamentos.inserir(self.item)

        for tipo in ("TECNICO", "SIGNATARIO", "SOLICITANTE"):
            lista = self.pessoas.listar(tipo)
            exemplo = ", ".join(p.nome for p in lista[:3]) + (" ..." if len(lista) > 3 else "")
            print(f"   {tipo:<12} {len(lista):>3} gravado(s): {exemplo}")
        for categoria in ("PADRAO", "ITEM_CALIBRADO"):
            lista = self.equipamentos.listar(categoria)
            print(f"   {categoria:<14} {len(lista):>1} gravado(s): {', '.join(e.codigo_tag for e in lista)}")

    def atualizar_e_excluir_cadastros(self) -> None:
        """Etapa 3: UPDATE e DELETE."""
        titulo("3) UPDATE e DELETE de cadastros")
        metrologista = self.report.cabecalho["metrologista"]

        # achado A-06: o técnico do Calypso recebe o nome completo e a matrícula do certificado
        tecnico = self.pessoas.buscar_por_nome(metrologista, "TECNICO")
        antes = f"nome='{tecnico.nome}', matrícula={tecnico.matricula}"
        tecnico.nome = self.comp["tecnico"]          # o setter valida e tira espaços sobrando
        tecnico.matricula = self.comp["matricula"]   # texto, para não perder o hífen (RN-07)
        self.pessoas.atualizar(tecnico)
        self.tecnico = self.pessoas.buscar_por_id(tecnico.id)
        print(f"   UPDATE técnico id={tecnico.id}: {antes}  ->  nome='{self.tecnico.nome}', "
              f"matrícula={self.tecnico.matricula}")
        print(f"   Continua reconhecendo o nome do Calypso '{metrologista}'? "
              f"{self.tecnico.reconhece_nome(metrologista)}")

        self.item.solicitante = self.solicitante
        self.equipamentos.atualizar(self.item)
        print(f"   UPDATE item {self.item.codigo_tag}: dono = área '{self.item.solicitante.nome}'")

        # registro de teste: entra e sai do banco (não tem nenhuma medição ligada, então o DELETE passa)
        teste = poo.Tecnico("Técnico de Teste MVP", matricula="99999-9", setor="Metrologia")
        self.pessoas.inserir(teste)
        print(f"   INSERT de teste: '{teste.nome}' gravado com id={teste.id}")
        self.pessoas.excluir(teste)
        print(f"   DELETE de teste: '{teste.nome}' excluído | técnicos no banco: "
              f"{len(self.pessoas.listar('TECNICO'))}")

    def importar_calibracao(self) -> None:
        """Etapa 4: Report do Calypso -> objetos Medicao -> INSERT numa transação."""
        titulo("4) Calibração 00091/2026: objetos Medicao, status e INSERT")
        comp, cab = self.comp, self.report.cabecalho
        padrao = self.equipamentos.buscar_por_codigo(extrator.codigo_padrao(comp["padrao"]))
        hora = cab.get("hora")

        self.calibracao = poo.Calibracao(
            comp["numero_certificado"], item=self.item, padrao=padrao, tecnico=self.tecnico,
            data_medicao=cab["data"], solicitante=self.solicitante, signatario=None,
            hora_medicao=hora.strftime("%H:%M:%S") if hora else None,
            plano_medicao=cab.get("plano_medicao"), fornecedor=cab.get("fornecedor"),
            incerteza_expandida=round(float(comp["incerteza_um"]) / 1000, 3),  # µm -> mm (achado A-09)
            fator_k=comp["fator_k"], temperatura_ambiente=comp["temperatura"],
            arquivo_origem=self.arquivo_calypso.name, hash_arquivo=extrator.sha256(self.arquivo_calypso))
        for c in self.report.caracteristicas:
            self.calibracao.nova_medicao(c.parametro, c.nominal, c.tol_sup, c.tol_inf, c.medidas)

        cal = self.calibracao
        print(f"   Objeto criado: {cal!r}")
        print(f"   Padrão {padrao.codigo_tag} válido em {cal.data_medicao}? {padrao.esta_valido_em(cal.data_medicao)}")
        print(f"   Como o status é calculado (U = {cal.incerteza_expandida} mm, com faixa de guarda):")
        for m in (cal.medicoes[2], cal.medicoes[3]):
            print(f"     {m.parametro:<16} obtido={m.valor_obtido:.4f}  limites=[{m.limite_inferior:.3f}; "
                  f"{m.limite_superior:.3f}]  obtido±U=[{m.valor_obtido - m.incerteza_expandida:.4f}; "
                  f"{m.valor_obtido + m.incerteza_expandida:.4f}]  -> {m.status}")
        if self.report.anomalias:
            print(f"   Anomalias encontradas na leitura do Calypso (Fase 02): {len(self.report.anomalias)}")

        ids = self.medicoes.inserir_calibracao(cal)
        print(f"   INSERT feito numa transação: {len(ids)} medições (id {ids[0]} a {ids[-1]})")

        # UPDATE permitido na medição: só os dados da emissão (signatário e data)
        n = self.medicoes.registrar_emissao(cal.numero_certificado, comp["data_emissao"], self.signatario)
        print(f"   UPDATE da emissão: {n} linhas com data {comp['data_emissao']} e signatário "
              f"'{self.signatario.nome}'")

    def consultas(self) -> None:
        """Etapa 5: consultas com JOIN."""
        titulo("5) Consultas relacionais com JOIN")
        numero, codigo_item = self.calibracao.numero_certificado, self.item.codigo_tag

        print("\n   a) Certificado: medicao JOIN equipamento (item e padrão) JOIN tecnico_solicitante (3 papéis)")
        linhas = self.conexao.consultar(
            """SELECT m.sequencia AS seq, m.parametro, e.codigo AS item, p.codigo AS padrao,
                      t.nome AS tecnico, s.nome AS solicitante, g.nome AS signatario, m.status
               FROM medicao m
               JOIN equipamento e              ON e.id_equipamento = m.id_equipamento
               JOIN equipamento p              ON p.id_equipamento = m.id_padrao
               JOIN tecnico_solicitante t      ON t.id_pessoa = m.id_tecnico
               LEFT JOIN tecnico_solicitante s ON s.id_pessoa = m.id_solicitante
               LEFT JOIN tecnico_solicitante g ON g.id_pessoa = m.id_signatario
               WHERE m.numero_certificado = ? AND m.sequencia <= 4
               ORDER BY m.sequencia""", (numero,))
        tabela(linhas, ["seq", "parametro", "item", "padrao", "tecnico", "solicitante", "signatario", "status"])
        print("      (só as 4 primeiras linhas; o certificado completo sai na etapa 6)")

        print(f"\n   b) Histórico de calibrações do item {codigo_item}")
        tabela(self.medicoes.historico_equipamento(codigo_item),
               ["numero_certificado", "data_medicao", "padrao", "tecnico", "parametros", "aprovados", "reprovados"])

        print("\n   c) Medições por técnico (LEFT JOIN: aparece também quem ainda não mediu nada)")
        tabela(self.medicoes.resumo_por_tecnico(), ["tecnico", "matricula", "certificados", "parametros", "reprovados"])

        print("\n   d) Uso e validade dos padrões")
        tabela(self.medicoes.uso_dos_padroes()[:4], ["codigo", "data_proxima_calibracao", "medicoes", "ultima_medicao"])
        print("      (4 dos 8 padrões; os outros estão sem certificado cadastrado, pendência P-07)")

    def relatorio(self) -> bool:
        """Etapa 6: relê o certificado do banco (JOIN) e imprime."""
        titulo("6) Relatório de calibração (objetos montados de novo a partir do banco)")
        relida = self.medicoes.buscar_calibracao(self.calibracao.numero_certificado)
        poo.RelatorioCalibracao(relida).imprimir()
        confere = all(m.status_confere_com_banco for m in relida.medicoes)
        print(f"   O status gravado bate com o recalculado pela classe Medicao em todas as linhas? {confere}")
        return confere

    def cenarios_de_erro(self) -> int:
        """Etapa 7: registros de teste errados de propósito. Todos têm que ser recusados."""
        titulo("7) Tratamento de erros: registros de teste que devem ser recusados")
        conexao, pessoas, medicoes = self.conexao, self.pessoas, self.medicoes
        cal, item, tecnico = self.calibracao, self.item, self.tecnico
        padrao = cal.padrao
        padrao_sem_cert = self.equipamentos.buscar_por_codigo("500.008")

        def nova(numero="00099/2026", data="2026-08-20", pad=padrao):
            c = poo.Calibracao(numero, item=item, padrao=pad, tecnico=tecnico, data_medicao=data,
                               incerteza_expandida=0.002)
            c.nova_medicao("TESTE_DIAMETRO", 6.0, 0.01, -0.01, [6.001, 6.002])
            return c

        casos = [
            ("Importar de novo o certificado 00091/2026 (RN-10)",
             lambda: medicoes.inserir_calibracao(medicoes.buscar_calibracao(cal.numero_certificado))),
            ("Calibração com o padrão 500.011 depois do vencimento, em 15/09/2026 (RN-05)",
             lambda: medicoes.inserir_calibracao(nova(data="2026-09-15"))),
            ("Calibração com a MMC 500.008, que não tem certificado cadastrado (RN-05, achado A-05)",
             lambda: medicoes.inserir_calibracao(nova(pad=padrao_sem_cert))),
            ("INSERT direto no banco, sem passar pelas classes, com padrão vencido (trigger RN-05)",
             lambda: conexao.executar(
                 "INSERT INTO medicao (numero_certificado, id_equipamento, id_padrao, id_tecnico, data_medicao, "
                 "sequencia, parametro, valor_nominal, tolerancia_superior, tolerancia_inferior, medida_1, "
                 "valor_obtido, erro, status) VALUES ('00099/2026', ?, ?, ?, '2026-09-15', 1, 'TESTE', 6, 0.01, "
                 "-0.01, 6.001, 6.001, 0.001, 'APROVADO')", (item.id, padrao.id, tecnico.id), "INSERT direto")),
            ("Medição com tolerância inferior maior que a superior (RN-09)",
             lambda: poo.Medicao("TESTE", 6.0, -0.01, 0.01, [6.0])),
            ("Medição sem nenhuma medida (RN-01)", lambda: poo.Medicao("TESTE", 6.0, 0.01, -0.01, [])),
            ("Técnico colocado no lugar do signatário (RN-13)",
             lambda: poo.Calibracao("00099/2026", item, padrao, tecnico, "2026-08-20", signatario=tecnico)),
            ("Número de certificado fora do padrão (RN-23)", lambda: nova(numero="91/26")),
            ("Matrícula como número, que perderia o hífen (RN-07)", lambda: poo.Tecnico("Teste", matricula=168580)),
            ("Matrícula 16858-0 repetida em outro técnico (UNIQUE)",
             lambda: pessoas.inserir(poo.Tecnico("Técnico de Teste", matricula="16858-0"))),
            ("Alterar um valor medido direto no banco (RN-02, trigger)",
             lambda: conexao.executar("UPDATE medicao SET medida_1 = 6.075 WHERE numero_certificado = ? "
                                      "AND sequencia = 3", (cal.numero_certificado,), "Alterar medida")),
            ("Excluir uma medição gravada (RN-25, trigger)",
             lambda: conexao.executar("DELETE FROM medicao WHERE sequencia = 1", (), "Excluir medição")),
            ("Excluir o técnico José, que já tem medições (RN-16, FOREIGN KEY)", lambda: pessoas.excluir(tecnico)),
            ("Excluir o item 703.1208, que tem histórico (RN-16, FOREIGN KEY)",
             lambda: self.equipamentos.excluir(item)),
            ("Buscar técnico que não existe (id 9999)", lambda: pessoas.buscar_por_id(9999)),
        ]
        acertos = sum(tentar(desc, acao) for desc, acao in casos)
        print(f"\n   {acertos} de {len(casos)} tentativas inválidas recusadas | medições no banco: "
              f"{medicoes.contar()} (nenhuma tentativa gravou nada)")
        return len(casos) - acertos


def main() -> int:
    p = argparse.ArgumentParser(description="Demonstração do MVP (Parte 1) - classes POO + SQLite")
    p.add_argument("--banco", default=str(BASE / "schulz_mvp.db"))
    p.add_argument("--complemento", default=str(DADOS / "complemento_00091-2026.json"))
    p.add_argument("--relatorio", metavar="CERTIFICADO",
                   help="só imprime o relatório de um certificado que já está no banco")
    a = p.parse_args()
    try:
        if a.relatorio:
            if not Path(a.banco).exists():
                print(f"ERRO: o banco {Path(a.banco).name} ainda não existe. Rode antes: python 05_main.py",
                      file=sys.stderr)
                return 1
            with poo.ConexaoSQLite(a.banco) as conexao:
                calibracao = poo.MedicaoRepositorio(conexao).buscar_calibracao(a.relatorio)
                poo.RelatorioCalibracao(calibracao).imprimir()
            return 0
        return Principal(a.banco, a.complemento).executar()
    except (poo.ErroSistema, extrator.ErroImportacao, FileNotFoundError) as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
