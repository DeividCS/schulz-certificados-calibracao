@echo off
REM ===================================================================
REM Projeto Aplicado I 2026.2 - UniSENAI | Schulz S.A. | Fase 04
REM Gera o executavel dist\Schulz_Calibracao.exe e a pasta de entrega.
REM Requisito: Python 3.10+ (x64) instalado com o "py launcher".
REM Uso: clique duas vezes neste arquivo ou rode "build_exe.bat" no terminal do VS Code.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo [1/5] Procurando o Python (3.10 a 3.14, x64) ...
set "PY="
REM instalador classico (python.org) e o novo "Python install manager" (pythoncore-3.xx-64)
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-%%V-64\python.exe" set "PY=%LOCALAPPDATA%\Python\pythoncore-%%V-64\python.exe"
)
for %%V in (314 313 312 311 310) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set "PY=%ProgramFiles%\Python%%V\python.exe"
)
if not defined PY (
    echo Python nao encontrado. Instalando o Python 3.12 x64 pelo winget ^(so para este usuario^) ...
    winget install -e --id Python.Python.3.12 --architecture x64 --scope user --silent --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
    echo Instale o Python 3.12 x64 em https://www.python.org/downloads/windows/ e rode de novo.
    goto :erro
)
echo Usando: %PY%
"%PY%" -c "import sys, sysconfig; print('Python', sys.version.split()[0], sysconfig.get_platform())"

echo Criando ambiente virtual .venv-build ...
if not exist .venv-build\Scripts\python.exe (
    "%PY%" -m venv .venv-build || goto :erro
)
call .venv-build\Scripts\activate.bat || goto :erro

echo [2/5] Instalando dependencias (openpyxl, reportlab, pyinstaller) ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt -r requirements-build.txt || goto :erro

echo [3/5] Gerando arquivos de teste e conferindo o codigo ...
python gerar_dados_teste.py || goto :erro
python testes_mvp.py || goto :erro

echo [4/5] Compilando com PyInstaller ...
pyinstaller --clean --noconfirm Schulz_Calibracao.spec || goto :erro

echo [5/5] Montando a pasta de entrega e rodando o plano de testes no .exe ...
if exist entrega rmdir /s /q entrega
mkdir entrega
copy /y dist\Schulz_Calibracao.exe entrega\ >nul
copy /y dados\04__Registro_MMC.xlsx entrega\ >nul
copy /y dados\complemento_00091-2026.json entrega\ >nul
copy /y dados\testes\Registro_MMC_aprovado_00092.xlsx entrega\ >nul
start /wait "" entrega\Schulz_Calibracao.exe --testes --pasta entrega
python -c "import sys,sysconfig; print('Python', sys.version.split()[0], sysconfig.get_platform())" > entrega\evidencia_build.txt
pyinstaller --version >> entrega\evidencia_build.txt
for %%F in (entrega\Schulz_Calibracao.exe) do echo Tamanho do exe: %%~zF bytes >> entrega\evidencia_build.txt
certutil -hashfile entrega\Schulz_Calibracao.exe SHA256 >> entrega\evidencia_build.txt
type entrega\resultados_testes\relatorio_testes.txt | findstr /C:"RESULTADO" >> entrega\evidencia_build.txt
type entrega\evidencia_build.txt

echo.
echo Pronto! Executavel em: %cd%\entrega\Schulz_Calibracao.exe
goto :fim

:erro
echo.
echo *** ERRO no build. Veja a mensagem acima. ***
pause
exit /b 1

:fim
pause
endlocal
