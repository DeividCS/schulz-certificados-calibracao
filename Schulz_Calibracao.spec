# -*- mode: python ; coding: utf-8 -*-
# Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A. | Fase 04
# Receita do PyInstaller para gerar dist\Schulz_Calibracao.exe (arquivo único, sem console).
# Uso: pyinstaller --clean --noconfirm Schulz_Calibracao.spec   (ou simplesmente: build_exe.bat)

dados = [
    ("03_banco.sql", "."),                                  # DDL da Fase 02 (cria as 3 tabelas)
    ("04_classes.py", "."),                                 # reserva: carregado do arquivo se preciso
    ("extrator.py", "."),
    ("dados/03__Planilha_MMC.xlsm", "dados"),               # cadastros (aba BANCO DE DADOS)
    ("dados/04__Registro_MMC.xlsx", "dados"),               # exemplo real do Calypso
    ("dados/padroes.csv", "dados"),
    ("dados/complemento_00091-2026.json", "dados"),
    ("dados/testes", "dados/testes"),                       # arquivos do plano de testes
]

a = Analysis(
    ["07_gerador_certificado.py"],
    pathex=["."],
    datas=dados,
    # "04_classes" é carregado com importlib (nome começa com número), então precisa ser declarado
    hiddenimports=["04_classes", "extrator", "openpyxl", "reportlab.graphics.barcode.common",
                   "reportlab.graphics.barcode.code128", "reportlab.graphics.barcode.code39",
                   "reportlab.graphics.barcode.code93", "reportlab.graphics.barcode.usps",
                   "reportlab.graphics.barcode.usps4s", "reportlab.graphics.barcode.ecc200datamatrix"],
    excludes=["matplotlib", "numpy", "pandas", "PIL.ImageQt", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Schulz_Calibracao",
    console=False,          # abre só a janela, sem o terminal preto
    upx=False,
    icon=None,
    version="versao_exe.txt",
)
