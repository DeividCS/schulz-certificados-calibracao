# 02 – Dicionário de Dados do Sistema

**Projeto Aplicado I 2026.2 – UniSENAI | Schulz S.A.**

Mapeamento de todos os campos gravados pelo script `extrator.py` no banco `schulz_calibracao.db`.
Os exemplos são os valores reais gravados na importação do certificado 00091/2026.

## Legenda

| Coluna | Significado |
|---|---|
| Tipo | Tipo de dado: **Texto** (`TEXT`), **Real** (`REAL`) ou **Inteiro** (`INTEGER`) |
| Chave | PK = chave primária · FK = chave estrangeira · UK = valor único |
| Obrig. | S = não aceita nulo |
| Obtenção | Como o script obtém o valor (ver tabela abaixo) |
| Origem | Arquivo e célula/coluna de onde o valor vem |

| Obtenção | Significado |
|---|---|
| **Extraído** | Lido pelo script da aba `Report` do Calypso (`dados/04__Registro_MMC.xlsx`) |
| **Cadastro** | Lido da aba `BANCO DE DADOS` (`dados/03__Planilha_MMC.xlsm`) ou de `dados/padroes.csv` |
| **Complemento** | Lido de `dados/complemento_00091-2026.json`: dados que não existem no Calypso e são informados na importação (RF-04). O arquivo indica a célula de origem de cada valor |
| **Calculado** | Calculado pelo script a partir de outros campos |
| **Automático** | Gerado pelo banco (chave ou data e hora atual) |

## Tabela `tecnico_solicitante` (8 campos)

| Campo | Tipo | Chave | Obrig. | Obtenção | Origem | Regra aplicada | Exemplo |
|---|---|---|---|---|---|---|---|
| id_pessoa | Inteiro | PK | S | Automático | Banco | Numeração automática | 1 |
| nome | Texto | UK (com tipo) | S | Cadastro / Complemento | BANCO DE DADOS col. B e E; Complemento!B76, Certificado!F61 | Espaços extras removidos; não pode ser vazio | Jose Alcides Rosa Junior |
| nome_abreviado | Texto | | | Extraído / Cadastro | `Report` "Metrologista"; BANCO DE DADOS col. B | Usado para relacionar o técnico, comparando sem acento e sem maiúsculas | José A. Rosa Jr |
| matricula | Texto | UK | | Complemento | Complemento!B77 | Guardada como texto para manter o hífen | 16858-0 |
| tipo | Texto | | S | Calculado | Papel na calibração | Somente `TECNICO`, `SIGNATARIO` ou `SOLICITANTE` | TECNICO |
| setor | Texto | | | Cadastro | BANCO DE DADOS col. E | Espaços extras removidos | Metrologia |
| ativo | Inteiro | | S | Automático | Banco (padrão 1) | Somente 0 ou 1 | 1 |
| data_cadastro | Texto | | S | Automático | Data e hora atual | Formato AAAA-MM-DD HH:MM:SS validado | 2026-10-04 17:09:10 |

## Tabela `equipamento` (15 campos)

| Campo | Tipo | Chave | Obrig. | Obtenção | Origem | Regra aplicada | Exemplo |
|---|---|---|---|---|---|---|---|
| id_equipamento | Inteiro | PK | S | Automático | Banco | Numeração automática | 9 |
| codigo | Texto | UK | S | Complemento / Cadastro | Complemento!B40; BANCO DE DADOS col. C; `padroes.csv` | Texto; `500008` vira `500.008` | 703.1208 |
| denominacao | Texto | | S | Complemento / Cadastro | Complemento!B41; `padroes.csv` (Padrões!C3) | Espaços extras removidos | Dispositivo de controle |
| categoria | Texto | | S | Calculado | Item do certificado ou padrão | Somente `ITEM_CALIBRADO` ou `PADRAO` | ITEM_CALIBRADO |
| fabricante | Texto | | | Complemento / Cadastro | Complemento!B43; `padroes.csv` (Padrões!G3) | "Não consta" vira nulo; a consulta do certificado reimprime "Não consta" | (nulo) / Carl Zeiss |
| modelo | Texto | | | Complemento / Cadastro | Complemento!B42; `padroes.csv` (Padrões!F3) | "Não se aplica" vira nulo | Contura G2 |
| numero_serie | Texto | | | Complemento / Cadastro | Complemento!B44; `padroes.csv` (Padrões!H3) | "---" vira nulo; texto para manter zeros | 502390 |
| unidade | Texto | | S | Complemento / Cadastro | Complemento!B56; `padroes.csv` | Somente `mm` ou `um` (micrometro) | mm |
| resolucao | Real | | | Complemento / Cadastro | Complemento!B55; `padroes.csv` (Padrões!J5) | "0,001" vira 0.001; maior que zero | 0.001 |
| certificado_calibracao | Texto | | | Cadastro | `padroes.csv` (Padrões!G5) | Texto | 241120502390 |
| data_ultima_calibracao | Texto | | | Cadastro / Complemento | `padroes.csv` (Padrões!F5); para o item, a data de emissão | "20/11/2024" vira 2024-11-20; formato validado | 2024-11-20 |
| data_proxima_calibracao | Texto | | | Cadastro / Complemento | `padroes.csv` (Padrões!I5); Complemento!B73 | Formato validado; não pode ser anterior à última; usada para bloquear padrão vencido | 2026-08-31 |
| periodicidade_meses | Inteiro | | | Calculado | Meses entre última e próxima calibração | Maior que zero | 24 |
| id_solicitante | Inteiro | FK → tecnico_solicitante | | Complemento | Complemento!B24 | Área dona do item | 34 |
| ativo | Inteiro | | S | Automático | Banco (padrão 1) | Somente 0 ou 1 | 1 |

## Tabela `medicao` (30 campos)

| Campo | Tipo | Chave | Obrig. | Obtenção | Origem | Regra aplicada | Exemplo |
|---|---|---|---|---|---|---|---|
| id_medicao | Inteiro | PK | S | Automático | Banco | Numeração automática | 3 |
| numero_certificado | Texto | UK (com parâmetro) | S | Complemento | Complemento!B75 | Texto com zeros e barra; certificado já importado é recusado | 00091/2026 |
| id_equipamento | Inteiro | FK → equipamento | S | Calculado | Código do item | Precisa ser `ITEM_CALIBRADO` | 9 |
| id_padrao | Inteiro | FK → equipamento | S | Complemento | Certificado!A20 | Precisa ser `PADRAO` e estar válido na data da medição | 3 |
| id_tecnico | Inteiro | FK → tecnico_solicitante | S | Extraído | `Report` "Metrologista" | Precisa ser do tipo `TECNICO` | 1 |
| id_solicitante | Inteiro | FK → tecnico_solicitante | | Complemento | Complemento!B24 | Precisa ser do tipo `SOLICITANTE` | 34 |
| id_signatario | Inteiro | FK → tecnico_solicitante | | Complemento | Certificado!F61 | Precisa ser do tipo `SIGNATARIO` | 33 |
| plano_medicao | Texto | | | Extraído | `Report` "Measurement Plan" | Espaços extras removidos | 703.1208_TESTE |
| fornecedor | Texto | | | Extraído | `Report` "Fornecedor" | Espaços extras removidos | CDM |
| data_medicao | Texto | | S | Extraído | `Report` "Date" | Data do Excel vira AAAA-MM-DD; formato validado | 2026-08-13 |
| hora_medicao | Texto | | | Extraído | `Report` "Time" | HH:MM:SS validado | 16:16:26 |
| data_emissao | Texto | | | Complemento | Complemento!B72 (26/08/2026 16:47) | Gravada só a data, no formato AAAA-MM-DD | 2026-08-26 |
| sequencia | Inteiro | | S | Calculado | Ordem no `Report` | Maior que zero | 3 |
| parametro | Texto | UK (com certificado) | S | Extraído | `Report` "Characteristic" | Espaços extras removidos; acentos mantidos | 62P_DIÂMETRO 1 |
| valor_nominal | Real | | S | Extraído | `Report` "Nominal" | Precisão total, sem arredondar | 6.075 |
| tolerancia_superior | Real | | S | Extraído | `Report` "Upper Tol" | Não pode ser menor que a inferior | 0.01 |
| tolerancia_inferior | Real | | S | Extraído | `Report` "Lower Tol" | Gravada com o sinal original | -0.01 |
| medida_1 | Real | | S | Extraído | `Report` "Actual", 1º bloco | Não pode ser alterada após gravada | 6.0787295 |
| medida_2 | Real | | | Extraído | `Report` "Actual", 2º bloco | Não pode ser alterada após gravada | 6.0787295 |
| valor_obtido | Real | | S | Calculado | Média de medida_1 e medida_2 | Sem arredondar no banco | 6.0787295 |
| erro | Real | | S | Calculado | valor_obtido − valor_nominal | O banco recusa erro incoerente | 0.0037295 |
| desvio_padrao | Real | | | Calculado | Desvio padrão amostral das medidas | Não negativo | 0.0 |
| incerteza_expandida | Real | | | Complemento + Calculado | Incerteza!G58 = 2,0769 µm | Dividida por 1000 e arredondada a 3 casas, como na planilha | 0.002 |
| fator_k | Real | | | Complemento | Incerteza!F58 | Maior que zero | 2.23 |
| temperatura_ambiente | Real | | | Complemento | Registro!H5 = "20,4°" | Símbolo ° removido e vírgula convertida; entre 10 e 30 °C | 20.4 |
| unidade | Texto | | S | Calculado | Título "4. Resultados (mm)" do certificado | Somente `mm` ou `um` | mm |
| status | Texto | | S | Calculado | Faixa de guarda da aba Registro, com sinal corrigido | `REPROVADO` se média + U > nominal + tol. superior ou média − U < nominal + tol. inferior; senão `APROVADO`. Não pode ser alterado | APROVADO |
| arquivo_origem | Texto | | | Calculado | Nome do arquivo importado | Rastreabilidade | 04__Registro_MMC.xlsx |
| hash_arquivo | Texto | | | Calculado | SHA-256 do arquivo | 64 caracteres; prova de que o arquivo não mudou | 1ce74b48… |
| data_importacao | Texto | | S | Automático | Data e hora atual | Formato validado | 2026-10-04 17:09:10 |

**Total:** 53 campos mapeados (8 + 15 + 30).

## Regras de conversão aplicadas pelo script

| Situação encontrada | Função no `extrator.py` | Resultado |
|---|---|---|
| Espaços no início, no fim ou repetidos | `limpar_texto` | Removidos |
| `---`, `------`, `N/A`, `00/00/0000`, `Não consta`, `Não se aplica`, `Não informado` | `limpar_texto` / `para_real` | Gravados como nulo, nunca como zero |
| Código numérico `500008` | `codigo_padrao` | Texto `500.008` |
| Número com vírgula ou símbolo (`0,001`, `20,4°`) | `para_real` | 0.001 e 20.4; texto com vírgula **e** ponto é recusado |
| Data do Excel, `DD/MM/AAAA` ou `AAAA-MM-DD` | `para_data_iso` | `AAAA-MM-DD`; data impossível é recusada |
| Nome com e sem acento | `chave_comparacao` | Comparado sem acento, gravado com acento |
| Deviation ≠ Actual − Nominal | `ler_report` | Registrado em `logs/`; o valor gravado é o Actual |
| Duas medições por parâmetro | `ler_report` | Unidas pelo nome do parâmetro (bloco 1 e bloco 2) |
| Report sem resultados | `ler_report` | Importação cancelada (RN-01) |
| Certificado já importado | `importar` | Importação cancelada sem alterar o banco (RN-10) |
