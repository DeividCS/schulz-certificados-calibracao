"""
Testes do banco e do script (rodar depois do extrator.py):
    python testes_regras.py

1) Estrutura: o banco tem exatamente as 3 tabelas do modelo.
2) Conformidade: o banco reproduz o certificado modelo 00091/2026.
3) Bloqueios: gravações e alterações inválidas são recusadas pelo banco.
4) Script: reimportação e arquivo sem resultados são recusados sem apagar o histórico.
Nenhum teste altera o banco: as tentativas são desfeitas e o item 4 usa uma cópia temporária.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from consultar import executar

BASE = Path(__file__).resolve().parent

# Evita erro de acentuação em consoles antigos do Windows
for _fluxo in (sys.stdout, sys.stderr):
    if hasattr(_fluxo, "reconfigure"):
        _fluxo.reconfigure(errors="replace")
BANCO = BASE / "schulz_calibracao.db"

# Valores impressos no certificado modelo 00091/2026 (valor obtido, erro) com 3 casas
ESPERADO = {
    "PLANICIDADE_REF": (0.002, 0.002), "ENTRE CENTROS_REF": (0.027, 0.027),
    "62P_DIÂMETRO 1": (6.079, 0.004), "62P_POSIÇÃO 1": (0.430, 0.430),
    "62P_DIÂMETRO 2": (6.078, 0.003), "62P_POSIÇÃO 2": (0.245, 0.245),
    "13P_DIÂMETRO 1": (16.968, 0.004), "13P_POSIÇÃO 1": (0.959, 0.959),
    "13P_DIÂMETRO 2": (16.970, 0.006), "13P_POSIÇÃO 2": (0.236, 0.236),
    "106P_DIÂMETRO 1": (6.346, 0.001), "106P_POSIÇÃO 1": (0.132, 0.132),
    "106P_DIÂMETRO 2": (6.349, 0.004), "106P_POSIÇÃO 2": (0.868, 0.868),
}

ID_ITEM = "(SELECT id_equipamento FROM equipamento WHERE codigo = '703.1208')"
ID_PADRAO = "(SELECT id_equipamento FROM equipamento WHERE codigo = '500.011')"
ID_TECNICO = "(SELECT id_pessoa FROM tecnico_solicitante WHERE tipo = 'TECNICO' AND matricula = '16858-0')"
ID_SIGNAT = "(SELECT id_pessoa FROM tecnico_solicitante WHERE tipo = 'SIGNATARIO' LIMIT 1)"


def inserir(**troca):
    v = dict(cert="'00099/2026'", item=ID_ITEM, padrao=ID_PADRAO, tecnico=ID_TECNICO, signat=ID_SIGNAT,
             data="'2026-08-13'", param="'TESTE'", tol_sup="0.05", tol_inf="0", obtido="0.002",
             erro="0.002", status="'APROVADO'")
    v.update(troca)
    return f"""INSERT INTO medicao (numero_certificado, id_equipamento, id_padrao, id_tecnico, id_signatario,
        data_medicao, sequencia, parametro, valor_nominal, tolerancia_superior, tolerancia_inferior,
        medida_1, valor_obtido, erro, status)
        VALUES ({v['cert']}, {v['item']}, {v['padrao']}, {v['tecnico']}, {v['signat']}, {v['data']}, 1,
                {v['param']}, 0, {v['tol_sup']}, {v['tol_inf']}, 0.002, {v['obtido']}, {v['erro']}, {v['status']})"""


BLOQUEIOS = [
    ("Importar o mesmo parâmetro duas vezes (RN-10)", inserir(cert="'00091/2026'", param="'PLANICIDADE_REF'")),
    ("Padrão vencido na data da medição (RN-05)", inserir(data="'2026-09-15'")),
    ("Padrão sem certificado/validade (RN-05)",
     inserir(padrao="(SELECT id_equipamento FROM equipamento WHERE codigo = '500.008')")),
    ("Usar o item calibrado como padrão", inserir(padrao=ID_ITEM)),
    ("Usar um padrão como item calibrado", inserir(item=ID_PADRAO)),
    ("Técnico inexistente (chave estrangeira)", inserir(tecnico="99999")),
    ("Área solicitante no lugar do técnico",
     inserir(tecnico="(SELECT id_pessoa FROM tecnico_solicitante WHERE nome = 'Usinagem I')")),
    ("Técnico no lugar do signatário (RN-13)", inserir(signat=ID_TECNICO)),
    ("Status fora do domínio", inserir(status="'OK'")),
    ("Tolerância invertida (RN-09)", inserir(tol_sup="-0.05", tol_inf="0.05")),
    ("Erro incoerente com obtido - nominal", inserir(erro="0.5")),
    ("Data da medição ausente (RN-01)", inserir(data="NULL")),
    ("Data fora do padrão AAAA-MM-DD", inserir(data="'13/08/2026'")),
    ("Alterar medida original (RN-02)", "UPDATE medicao SET medida_1 = 1 WHERE sequencia = 1"),
    ("Alterar status manualmente (RN-02)",
     "UPDATE medicao SET status = 'APROVADO' WHERE parametro = '62P_POSIÇÃO 1'"),
    ("Trocar para padrão sem validade (RN-05)",
     "UPDATE medicao SET id_padrao = (SELECT id_equipamento FROM equipamento WHERE codigo = '500.008') "
     "WHERE sequencia = 2"),
    ("Alterar data para formato inválido", "UPDATE medicao SET data_medicao = '13/08/2026' WHERE sequencia = 3"),
    ("Excluir medição (RN-25)", "DELETE FROM medicao WHERE sequencia = 1"),
    ("Excluir equipamento com medições (RN-16)", "DELETE FROM equipamento WHERE codigo = '703.1208'"),
]


def rodar_script(*args, banco):
    # Força UTF-8 no processo filho: no Windows o console usa cp1252 e os acentos
    # das mensagens chegariam corrompidos, quebrando a leitura da saída.
    ambiente = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    r = subprocess.run([sys.executable, str(BASE / "extrator.py"), "--banco", str(banco), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=ambiente)
    return r.returncode, (r.stderr or r.stdout).strip().splitlines()[-1]


def main() -> int:
    if not BANCO.exists():
        print("Rode primeiro: python extrator.py")
        return 1
    falhas = 0
    con = sqlite3.connect(BANCO)
    con.execute("PRAGMA foreign_keys = ON")

    print("1) Estrutura do banco")
    objetos = con.execute("SELECT type, name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name").fetchall()
    ok = objetos == [("table", "equipamento"), ("table", "medicao"), ("table", "tecnico_solicitante")]
    falhas += not ok
    print(f"   [{'OK ' if ok else 'ERRO'}] tabelas e visões no banco: {[n for _, n in objetos]}")

    print("\n2) Comparação com o certificado modelo 00091/2026")
    colunas, linhas = executar("certificado", {"certificado": "00091/2026"}, BANCO)
    ip, io, ie = colunas.index("parametro"), colunas.index("valor_obtido"), colunas.index("erro")
    for l in linhas:
        ok = ESPERADO.get(l[ip]) == (l[io], l[ie])
        falhas += not ok
        print(f"   [{'OK ' if ok else 'ERRO'}] {l[ip]:<18} obtido={l[io]:<7} erro={l[ie]}")
    if len(linhas) != len(ESPERADO):
        falhas += 1
        print(f"   [ERRO] esperado {len(ESPERADO)} parâmetros, banco tem {len(linhas)}")

    print("\n3) Regras que devem BLOQUEAR a gravação ou a alteração")
    for nome, sql in BLOQUEIOS:
        try:
            con.execute(sql)
            falhas += 1
            print(f"   [ERRO] {nome}: o banco aceitou")
        except sqlite3.DatabaseError as e:
            print(f"   [OK ] {nome} -> {e}")
        finally:
            con.rollback()
    con.close()

    print("\n4) Comportamento do script de importação")
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "copia.db"
        shutil.copy(BANCO, copia)
        rc, msg = rodar_script(banco=copia)
        ok = rc == 1 and "RN-10" in msg
        falhas += not ok
        print(f"   [{'OK ' if ok else 'ERRO'}] Reimportar o mesmo certificado -> {msg}")
        n = sqlite3.connect(copia).execute("SELECT COUNT(*) FROM medicao").fetchone()[0]
        ok = n == len(ESPERADO)
        falhas += not ok
        print(f"   [{'OK ' if ok else 'ERRO'}] Histórico preservado após a tentativa: {n} medições")
        rc, msg = rodar_script("--registro", str(BASE / "dados" / "03__Planilha_MMC.xlsm"), banco=Path(tmp) / "v.db")
        ok = rc == 1 and "RN-01" in msg
        falhas += not ok
        print(f"   [{'OK ' if ok else 'ERRO'}] Arquivo sem resultados (Planilha MMC 2022) -> {msg}")

    total = 1 + len(ESPERADO) + len(BLOQUEIOS) + 3
    print(f"\nResultado: {total - falhas} de {total} verificações aprovadas")
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
