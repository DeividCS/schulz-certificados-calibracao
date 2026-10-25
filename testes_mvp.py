"""
Testes automatizados do MVP (Parte 1) - classes de 04_classes.py.

    python testes_mvp.py        (ou: python -m unittest testes_mvp -v)

Cada teste usa um banco temporário próprio; nenhum arquivo do projeto é alterado.
Grupos:
  A) Regra de status da classe Medicao (limites, faixa de guarda, cálculos)
  B) Encapsulamento e validação das classes de domínio
  C) Persistência: INSERT, SELECT, UPDATE, JOIN, transação e tradução de erros do SQLite
  D) Conformidade: o MVP reproduz os 14 resultados do certificado modelo 00091/2026
"""
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

poo = importlib.import_module("04_classes")
BASE = Path(__file__).resolve().parent

for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(errors="replace")


def medicao(nominal=6.0, tol_sup=0.01, tol_inf=-0.01, medidas=(6.0, 6.0), u=0.002, regra="FAIXA_GUARDA"):
    return poo.Medicao("TESTE", nominal, tol_sup, tol_inf, list(medidas), incerteza_expandida=u, regra_decisao=regra)


# ----------------------------------------------------------------------
class A_RegraDeStatus(unittest.TestCase):
    def test_media_erro_e_desvio(self):
        m = medicao(medidas=(6.004, 6.006))
        self.assertAlmostEqual(m.valor_obtido, 6.005)
        self.assertAlmostEqual(m.erro, 0.005)
        self.assertAlmostEqual(m.desvio_padrao, 0.0014142, places=6)

    def test_uma_medida_nao_tem_desvio(self):
        self.assertIsNone(medicao(medidas=(6.0,)).desvio_padrao)

    def test_dentro_da_tolerancia_aprova(self):
        self.assertEqual(medicao(medidas=(6.004, 6.004)).status, "APROVADO")

    def test_faixa_de_guarda_reprova_perto_do_limite_superior(self):
        # 6,009 + U (0,002) = 6,011 > 6,010
        self.assertEqual(medicao(medidas=(6.009, 6.009)).status, "REPROVADO")

    def test_regra_simples_aprova_o_mesmo_valor(self):
        self.assertEqual(medicao(medidas=(6.009, 6.009), regra="SIMPLES").status, "APROVADO")

    def test_limite_inferior_usa_tolerancia_com_sinal(self):
        # achado A-03: limite inferior = 6,000 + (-0,010) = 5,990
        m = medicao(medidas=(5.995, 5.995))
        self.assertAlmostEqual(m.limite_inferior, 5.990)
        self.assertEqual(m.status, "APROVADO")
        self.assertEqual(medicao(medidas=(5.991, 5.991)).status, "REPROVADO")

    def test_margem_de_float_no_limite(self):
        # 0,7 + 0,1 dá 0,7999999999999999 no float; sem a margem, medir exatamente 0,8 reprovaria
        m = medicao(nominal=0.7, tol_sup=0.1, tol_inf=0, medidas=(0.8,), regra="SIMPLES")
        self.assertGreater(m.valor_obtido, m.limite_superior)  # o float passa um pouquinho do limite
        self.assertEqual(m.status, "APROVADO")

    def test_tolerancia_unilateral_posicao(self):
        # parâmetros de posição: nominal 0, tol + 0,100, tol - 0
        self.assertEqual(medicao(0, 0.1, 0, (0.05, 0.05)).status, "APROVADO")
        self.assertEqual(medicao(0, 0.1, 0, (0.43, 0.43)).status, "REPROVADO")


class B_DominioEEncapsulamento(unittest.TestCase):
    def test_medicao_e_imutavel(self):
        m = medicao()
        with self.assertRaises(AttributeError):
            m.valor_nominal = 1.0  # sem setter (RN-02)

    def test_tolerancia_invertida(self):
        with self.assertRaises(poo.ErroValidacao) as ctx:
            medicao(tol_sup=-0.01, tol_inf=0.01)
        self.assertEqual(ctx.exception.regra, "RN-09")

    def test_numero_com_virgula_e_aceito(self):
        self.assertAlmostEqual(poo.Medicao("T", "6,075", "0,010", "-0,010", ["6,079"]).valor_nominal, 6.075)

    def test_codigo_numerico_do_calypso_vira_texto(self):
        self.assertEqual(poo.Padrao(500008, "MMC").codigo_tag, "500.008")
        with self.assertRaises(poo.ErroValidacao):
            poo.Padrao(500.011, "MMC")  # decimal perderia o zero

    def test_matricula_texto_preserva_hifen(self):
        self.assertEqual(poo.Tecnico("A", matricula="16858-0").matricula, "16858-0")
        with self.assertRaises(poo.ErroValidacao):
            poo.Tecnico("A", matricula=168580)

    def test_nome_vazio_e_marcador_de_vazio(self):
        with self.assertRaises(poo.ErroValidacao):
            poo.Tecnico("   ")
        self.assertIsNone(poo.ItemCalibrado("703.1208", "Dispositivo", fabricante="Não consta").fabricante)

    def test_reconhece_nome_abreviado_sem_acento(self):
        t = poo.Tecnico("Jose Alcides Rosa Junior", nome_abreviado="José A. Rosa Jr")
        self.assertTrue(t.reconhece_nome("JOSE A. ROSA JR"))
        self.assertFalse(t.reconhece_nome("Cleocir de Souza"))

    def test_polimorfismo_do_papel(self):
        papeis = [p.papel() for p in (poo.Tecnico("A"), poo.Signatario("B"), poo.Solicitante("C"))]
        self.assertEqual(papeis, ["Técnico executante", "Signatário autorizado", "Área solicitante"])

    def test_padrao_validade(self):
        p = poo.Padrao("500.011", "MMC", certificado_calibracao="241120502390",
                       data_ultima_calibracao="20/11/2024", data_proxima_calibracao="31/08/2026")
        self.assertTrue(p.esta_valido_em("2026-08-31"))
        self.assertFalse(p.esta_valido_em("2026-09-01"))
        self.assertFalse(poo.Padrao("500.008", "MMC").esta_valido_em("2026-08-13"))

    def test_calibracao_exige_tipos_corretos(self):
        item, pad, tec = poo.ItemCalibrado("1", "Item"), poo.Padrao("2", "MMC"), poo.Tecnico("T")
        with self.assertRaises(poo.ErroValidacao):
            poo.Calibracao("00001/2026", item=pad, padrao=pad, tecnico=tec, data_medicao="2026-01-01")
        with self.assertRaises(poo.ErroValidacao):
            poo.Calibracao("00001/2026", item, pad, tec, "2026-01-01", signatario=tec)

    def test_parecer_final_exige_todos_aprovados(self):
        cal = poo.Calibracao("00001/2026", poo.ItemCalibrado("1", "Item"), poo.Padrao("2", "MMC"),
                             poo.Tecnico("T"), "2026-01-01", incerteza_expandida=0.002)
        cal.nova_medicao("P1", 0, 0.1, 0, [0.01])
        self.assertEqual(cal.parecer_final, "APROVADO")
        cal.nova_medicao("P2", 0, 0.1, 0, [0.5])
        self.assertEqual(cal.parecer_final, "REPROVADO")

    def test_parametro_repetido_na_calibracao(self):
        cal = poo.Calibracao("00001/2026", poo.ItemCalibrado("1", "Item"), poo.Padrao("2", "MMC"),
                             poo.Tecnico("T"), "2026-01-01")
        cal.nova_medicao("P1", 0, 0.1, 0, [0.01])
        with self.assertRaises(poo.ErroValidacao):
            cal.nova_medicao("P1", 0, 0.1, 0, [0.02])
        self.assertEqual(cal.medicoes[0].sequencia, 1)


# ----------------------------------------------------------------------
class C_Persistencia(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = poo.ConexaoSQLite(Path(self.tmp.name) / "teste.db").abrir()
        self.pessoas = poo.TecnicoRepositorio(self.con)
        self.equip = poo.EquipamentoRepositorio(self.con)
        self.medicoes = poo.MedicaoRepositorio(self.con)
        self.tec = poo.Tecnico("Técnico Teste", matricula="100-1")
        self.sig = poo.Signatario("Signatário Teste")
        self.sol = poo.Solicitante("Área Teste")
        for p in (self.tec, self.sig, self.sol):
            self.pessoas.inserir(p)
        self.pad = poo.Padrao("900.001", "MMC Teste", certificado_calibracao="C-1",
                              data_ultima_calibracao="2026-01-01", data_proxima_calibracao="2026-12-31")
        self.item = poo.ItemCalibrado("800.001", "Item Teste", solicitante=self.sol)
        self.equip.inserir(self.pad)
        self.equip.inserir(self.item)

    def tearDown(self):
        self.con.fechar()
        self.tmp.cleanup()

    def calibracao(self, numero="00001/2026", data="2026-06-01", medidas=((6.001, 6.002), (0.5, 0.5))):
        cal = poo.Calibracao(numero, self.item, self.pad, self.tec, data, solicitante=self.sol,
                             signatario=self.sig, incerteza_expandida=0.002)
        cal.nova_medicao("DIAMETRO", 6.0, 0.01, -0.01, list(medidas[0]))
        cal.nova_medicao("POSICAO", 0.0, 0.1, 0.0, list(medidas[1]))
        return cal

    def test_estrutura_tem_as_3_tabelas(self):
        self.assertEqual(self.con.listar_tabelas(), ["equipamento", "medicao", "tecnico_solicitante"])

    def test_insert_e_select_reconstroem_subclasses(self):
        self.assertIsInstance(self.pessoas.buscar_por_id(self.sig.id), poo.Signatario)
        relido = self.equip.buscar_por_codigo("800.001")
        self.assertIsInstance(relido, poo.ItemCalibrado)
        self.assertEqual(relido.solicitante.nome, "Área Teste")

    def test_update_de_tecnico(self):
        self.tec.setor = "Metrologia"
        self.pessoas.atualizar(self.tec)
        self.assertEqual(self.pessoas.buscar_por_id(self.tec.id).setor, "Metrologia")

    def test_update_de_padrao_recalibrado(self):
        self.pad.registrar_calibracao("2026-12-20", "2027-12-31", certificado="C-2")
        self.equip.atualizar(self.pad)
        relido = self.equip.buscar_por_codigo("900.001")
        self.assertEqual((relido.certificado_calibracao, relido.data_proxima_calibracao), ("C-2", "2027-12-31"))

    def test_insert_calibracao_e_join(self):
        ids = self.medicoes.inserir_calibracao(self.calibracao())
        self.assertEqual(len(ids), 2)
        relida = self.medicoes.buscar_calibracao("00001/2026")
        self.assertEqual([m.status for m in relida.medicoes], ["APROVADO", "REPROVADO"])
        self.assertTrue(all(m.status_confere_com_banco for m in relida.medicoes))
        self.assertEqual(relida.tecnico.nome, "Técnico Teste")
        self.assertEqual(relida.signatario.nome, "Signatário Teste")
        hist = self.medicoes.historico_equipamento("800.001")
        self.assertEqual((hist[0]["aprovados"], hist[0]["reprovados"]), (1, 1))

    def test_reimportacao_recusada(self):
        self.medicoes.inserir_calibracao(self.calibracao())
        with self.assertRaises(poo.RegistroDuplicado):
            self.medicoes.inserir_calibracao(self.calibracao())
        self.assertEqual(self.medicoes.contar(), 2)

    def test_padrao_vencido_recusado_pela_classe(self):
        with self.assertRaises(poo.ErroValidacao) as ctx:
            self.medicoes.inserir_calibracao(self.calibracao(data="2027-01-15"))
        self.assertEqual(ctx.exception.regra, "RN-05")

    def test_transacao_desfaz_carga_parcial(self):
        # RN-29: o 2o comando falha (tipo inválido) e o 1o, que já tinha sido executado, é desfeito
        cal = self.calibracao()
        with self.assertRaises(poo.ErroPersistencia):
            with self.con.transacao():
                self.con.executar("INSERT INTO tecnico_solicitante (nome, tipo) VALUES ('X', 'TECNICO')")
                self.con.executar("INSERT INTO tecnico_solicitante (nome, tipo) VALUES ('Y', 'INVALIDO')")
        self.assertIsNone(self.pessoas.buscar_por_nome("X", "TECNICO"))  # a primeira foi desfeita
        self.medicoes.inserir_calibracao(cal)
        self.assertEqual(self.medicoes.contar(), 2)

    def test_traducao_de_erros_do_sqlite(self):
        self.medicoes.inserir_calibracao(self.calibracao())
        casos = [
            ("UPDATE medicao SET medida_1 = 1", poo.RegraNegocioViolada, "RN-02"),
            ("DELETE FROM medicao", poo.RegraNegocioViolada, "RN-25"),
            ("DELETE FROM equipamento WHERE codigo = '800.001'", poo.ReferenciaInvalida, "RN-22"),
            ("UPDATE medicao SET data_medicao = '13/08/2026'", poo.RegraNegocioViolada, None),
            ("INSERT INTO equipamento (codigo, denominacao, categoria) VALUES ('900.001', 'x', 'PADRAO')",
             poo.RegistroDuplicado, "RN-10"),
        ]
        for sql, classe, regra in casos:
            with self.subTest(sql=sql):
                with self.assertRaises(classe) as ctx:
                    self.con.executar(sql)
                self.assertEqual(ctx.exception.regra, regra)
                self.assertIsNotNone(ctx.exception.original)

    def test_registrar_emissao(self):
        self.medicoes.inserir_calibracao(self.calibracao())
        self.assertEqual(self.medicoes.registrar_emissao("00001/2026", "2026-06-05", self.sig), 2)
        self.assertEqual(self.medicoes.buscar_calibracao("00001/2026").data_emissao, "2026-06-05")
        with self.assertRaises(poo.ErroValidacao):
            self.medicoes.registrar_emissao("00001/2026", "2026-06-05", self.tec)

    def test_delete_sem_historico_e_bloqueio_com_historico(self):
        avulso = poo.Tecnico("Sem Medição", matricula="200-2")
        self.pessoas.inserir(avulso)
        self.pessoas.excluir(avulso)
        self.assertIsNone(self.pessoas.buscar_por_matricula("200-2"))
        self.medicoes.inserir_calibracao(self.calibracao())
        with self.assertRaises(poo.ReferenciaInvalida):
            self.pessoas.excluir(self.tec)
        with self.assertRaises(poo.ReferenciaInvalida):
            self.equip.excluir(self.item)

    def test_desativar_mantem_historico(self):
        self.medicoes.inserir_calibracao(self.calibracao())
        self.pessoas.desativar(self.tec)
        self.assertFalse(self.pessoas.buscar_por_id(self.tec.id).ativo)
        self.assertEqual(self.medicoes.contar(), 2)
        with self.assertRaises(poo.ErroValidacao):  # técnico inativo não faz calibração nova
            self.medicoes.inserir_calibracao(self.calibracao(numero="00002/2026"))

    def test_busca_inexistente(self):
        with self.assertRaises(poo.RegistroNaoEncontrado):
            self.pessoas.buscar_por_id(999)
        with self.assertRaises(poo.RegistroNaoEncontrado):
            self.medicoes.buscar_calibracao("99999/2026")


# ----------------------------------------------------------------------
class D_ConformidadeComOCertificado(unittest.TestCase):
    """Roda o 05_main.py e confere os valores impressos com o certificado modelo 00091/2026."""

    ESPERADO = [  # (parâmetro, obtido, erro) com 3 casas, como no certificado
        ("PLANICIDADE_REF", "0,002", "0,002"), ("ENTRE CENTROS_REF", "0,027", "0,027"),
        ("62P_DIÂMETRO 1", "6,079", "0,004"), ("62P_POSIÇÃO 1", "0,430", "0,430"),
        ("62P_DIÂMETRO 2", "6,078", "0,003"), ("62P_POSIÇÃO 2", "0,245", "0,245"),
        ("13P_DIÂMETRO 1", "16,968", "0,004"), ("13P_POSIÇÃO 1", "0,959", "0,959"),
        ("13P_DIÂMETRO 2", "16,970", "0,006"), ("13P_POSIÇÃO 2", "0,236", "0,236"),
        ("106P_DIÂMETRO 1", "6,346", "0,001"), ("106P_POSIÇÃO 1", "0,132", "0,132"),
        ("106P_DIÂMETRO 2", "6,349", "0,004"), ("106P_POSIÇÃO 2", "0,868", "0,868"),
    ]

    def test_demonstracao_reproduz_o_certificado(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, str(BASE / "05_main.py"), "--banco", str(Path(tmp) / "d.db")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        linhas = [l for l in r.stdout.splitlines() if "," in l]
        for parametro, obtido, erro in self.ESPERADO:
            with self.subTest(parametro=parametro):
                linha = next(l for l in linhas if f" {parametro} " in l)
                self.assertEqual(linha.split()[-3:-1], [obtido, erro])  # ... Obtido Erro Status
        self.assertIn("15 de 15 tentativas inválidas recusadas", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
