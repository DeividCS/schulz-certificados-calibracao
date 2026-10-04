-- =====================================================================
-- Projeto Aplicado I 2026.2 - UniSENAI | Empresa parceira: Schulz S.A.
-- Fase 02 - Modelagem e Implementacao do Banco de Dados (SQLite)
-- Arquivo: 03_banco.sql  (DDL - somente estrutura)
--
-- Cria exatamente 3 tabelas, conforme o roteiro:
--   1) tecnico_solicitante  -> tecnico, signatario e area solicitante
--   2) equipamento          -> item calibrado e padroes (ex.: MMC 500.011)
--   3) medicao              -> um registro por parametro avaliado no certificado
--
-- Observacoes:
--   * Comentarios e mensagens sem acento de proposito, para evitar
--     problemas de codificacao em terminais Windows.
--   * Datas validadas com "date(x) IS x": date() devolve NULL para texto
--     invalido, e um CHECK com resultado NULL seria aceito se fosse "=".
--   * INTEGER PRIMARY KEY ja numera automaticamente no SQLite; nao se usa
--     AUTOINCREMENT para nao criar a tabela interna sqlite_sequence.
--   * Consultas (como a do certificado) ficam em 04_consultas.sql, fora
--     do banco, para que ele contenha somente as 3 tabelas.
--
-- Execucao:  python extrator.py --somente-estrutura
--      ou :  sqlite3 schulz_calibracao.db < 03_banco.sql
-- =====================================================================

PRAGMA foreign_keys = ON;   -- o SQLite so valida FKs com esta opcao ligada

DROP TABLE IF EXISTS medicao;
DROP TABLE IF EXISTS equipamento;
DROP TABLE IF EXISTS tecnico_solicitante;

-- ---------------------------------------------------------------------
-- 1) TECNICO / SOLICITANTE
-- Fonte: aba "BANCO DE DADOS" (colunas B e E), Report!B11 e certificado.
-- ---------------------------------------------------------------------
CREATE TABLE tecnico_solicitante (
    id_pessoa       INTEGER PRIMARY KEY,
    nome            TEXT    NOT NULL CHECK (length(trim(nome)) > 0),
    nome_abreviado  TEXT,                       -- como aparece no Calypso
    matricula       TEXT    UNIQUE,             -- texto: preserva hifen e zeros
    tipo            TEXT    NOT NULL
                    CHECK (tipo IN ('TECNICO', 'SIGNATARIO', 'SOLICITANTE')),
    setor           TEXT,
    ativo           INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    data_cadastro   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                    CHECK (datetime(data_cadastro) IS data_cadastro),
    UNIQUE (nome, tipo)
);

-- ---------------------------------------------------------------------
-- 2) EQUIPAMENTO
-- Item calibrado (ex.: 703.1208) e padroes (ex.: 500.011).
-- Fonte: abas Complemento, Padroes e BANCO DE DADOS (coluna C).
-- ---------------------------------------------------------------------
CREATE TABLE equipamento (
    id_equipamento          INTEGER PRIMARY KEY,
    codigo                  TEXT    NOT NULL UNIQUE,   -- texto: "500.011" nao vira 500011
    denominacao             TEXT    NOT NULL,
    categoria               TEXT    NOT NULL
                            CHECK (categoria IN ('ITEM_CALIBRADO', 'PADRAO')),
    fabricante              TEXT,
    modelo                  TEXT,
    numero_serie            TEXT,
    unidade                 TEXT    NOT NULL DEFAULT 'mm'
                            CHECK (unidade IN ('mm', 'um')),   -- 'um' = micrometro
    resolucao               REAL    CHECK (resolucao IS NULL OR resolucao > 0),
    certificado_calibracao  TEXT,
    data_ultima_calibracao  TEXT    CHECK (data_ultima_calibracao IS NULL
                                           OR date(data_ultima_calibracao) IS data_ultima_calibracao),
    data_proxima_calibracao TEXT    CHECK (data_proxima_calibracao IS NULL
                                           OR date(data_proxima_calibracao) IS data_proxima_calibracao),
    periodicidade_meses     INTEGER CHECK (periodicidade_meses IS NULL OR periodicidade_meses > 0),
    id_solicitante          INTEGER,
    ativo                   INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    CHECK (data_proxima_calibracao IS NULL
           OR data_ultima_calibracao IS NULL
           OR data_proxima_calibracao >= data_ultima_calibracao),
    FOREIGN KEY (id_solicitante) REFERENCES tecnico_solicitante (id_pessoa)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

-- ---------------------------------------------------------------------
-- 3) MEDICAO
-- Uma linha por parametro avaliado (ex.: "62P_DIAMETRO 1").
-- Fonte: aba Report do Calypso (2 blocos = medida 1 e medida 2)
--        + dados complementares do certificado.
-- ---------------------------------------------------------------------
CREATE TABLE medicao (
    id_medicao           INTEGER PRIMARY KEY,
    numero_certificado   TEXT    NOT NULL,
    id_equipamento       INTEGER NOT NULL,       -- item calibrado
    id_padrao            INTEGER NOT NULL,       -- padrao utilizado (MMC)
    id_tecnico           INTEGER NOT NULL,       -- tecnico executante
    id_solicitante       INTEGER,                -- area solicitante
    id_signatario        INTEGER,                -- signatario autorizado
    plano_medicao        TEXT,
    fornecedor           TEXT,
    data_medicao         TEXT    NOT NULL CHECK (date(data_medicao) IS data_medicao),
    hora_medicao         TEXT    CHECK (hora_medicao IS NULL OR time(hora_medicao) IS hora_medicao),
    data_emissao         TEXT    CHECK (data_emissao IS NULL OR date(data_emissao) IS data_emissao),
    sequencia            INTEGER NOT NULL CHECK (sequencia > 0),
    parametro            TEXT    NOT NULL CHECK (length(trim(parametro)) > 0),
    valor_nominal        REAL    NOT NULL,
    tolerancia_superior  REAL    NOT NULL,
    tolerancia_inferior  REAL    NOT NULL,
    medida_1             REAL    NOT NULL,
    medida_2             REAL,
    valor_obtido         REAL    NOT NULL,       -- media das medidas
    erro                 REAL    NOT NULL,       -- valor_obtido - valor_nominal
    desvio_padrao        REAL    CHECK (desvio_padrao IS NULL OR desvio_padrao >= 0),
    incerteza_expandida  REAL    CHECK (incerteza_expandida IS NULL OR incerteza_expandida >= 0),
    fator_k              REAL    CHECK (fator_k IS NULL OR fator_k > 0),
    temperatura_ambiente REAL    CHECK (temperatura_ambiente IS NULL
                                        OR temperatura_ambiente BETWEEN 10 AND 30),
    unidade              TEXT    NOT NULL DEFAULT 'mm' CHECK (unidade IN ('mm', 'um')),
    status               TEXT    NOT NULL CHECK (status IN ('APROVADO', 'REPROVADO')),
    arquivo_origem       TEXT,
    hash_arquivo         TEXT    CHECK (hash_arquivo IS NULL OR length(hash_arquivo) = 64),
    data_importacao      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                         CHECK (datetime(data_importacao) IS data_importacao),

    CHECK (tolerancia_inferior <= tolerancia_superior),
    CHECK (abs(erro - (valor_obtido - valor_nominal)) < 1e-9),   -- erro coerente
    UNIQUE (numero_certificado, parametro),                       -- sem duplicidade

    FOREIGN KEY (id_equipamento) REFERENCES equipamento (id_equipamento)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (id_padrao)      REFERENCES equipamento (id_equipamento)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (id_tecnico)     REFERENCES tecnico_solicitante (id_pessoa)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (id_solicitante) REFERENCES tecnico_solicitante (id_pessoa)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (id_signatario)  REFERENCES tecnico_solicitante (id_pessoa)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE INDEX idx_medicao_certificado ON medicao (numero_certificado);
CREATE INDEX idx_medicao_equipamento ON medicao (id_equipamento, data_medicao);
CREATE INDEX idx_medicao_padrao      ON medicao (id_padrao);
CREATE INDEX idx_medicao_hash        ON medicao (hash_arquivo);

-- ---------------------------------------------------------------------
-- REGRAS DE NEGOCIO (Fase 01) aplicadas por triggers
-- Cada regra vale tanto na insercao quanto em uma alteracao posterior.
-- ---------------------------------------------------------------------

-- RN-05: padrao precisa ser PADRAO e estar valido na data da medicao
CREATE TRIGGER trg_medicao_padrao_ins
BEFORE INSERT ON medicao
BEGIN
    SELECT RAISE(ABORT, 'RN-05: equipamento informado como padrao nao e da categoria PADRAO')
    WHERE (SELECT categoria FROM equipamento WHERE id_equipamento = NEW.id_padrao) <> 'PADRAO';
    SELECT RAISE(ABORT, 'RN-05: padrao sem data de validade ou vencido na data da medicao')
    WHERE COALESCE((SELECT data_proxima_calibracao FROM equipamento
                    WHERE id_equipamento = NEW.id_padrao), '') < NEW.data_medicao;
END;

CREATE TRIGGER trg_medicao_padrao_upd
BEFORE UPDATE OF id_padrao, data_medicao ON medicao
BEGIN
    SELECT RAISE(ABORT, 'RN-05: equipamento informado como padrao nao e da categoria PADRAO')
    WHERE (SELECT categoria FROM equipamento WHERE id_equipamento = NEW.id_padrao) <> 'PADRAO';
    SELECT RAISE(ABORT, 'RN-05: padrao sem data de validade ou vencido na data da medicao')
    WHERE COALESCE((SELECT data_proxima_calibracao FROM equipamento
                    WHERE id_equipamento = NEW.id_padrao), '') < NEW.data_medicao;
END;

-- Papeis: item calibrado, tecnico, solicitante e signatario do tipo certo
CREATE TRIGGER trg_medicao_papeis_ins
BEFORE INSERT ON medicao
BEGIN
    SELECT RAISE(ABORT, 'Item calibrado deve ser da categoria ITEM_CALIBRADO')
    WHERE (SELECT categoria FROM equipamento WHERE id_equipamento = NEW.id_equipamento) <> 'ITEM_CALIBRADO';
    SELECT RAISE(ABORT, 'id_tecnico deve apontar para uma pessoa do tipo TECNICO')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_tecnico) <> 'TECNICO';
    SELECT RAISE(ABORT, 'id_solicitante deve apontar para um cadastro do tipo SOLICITANTE')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_solicitante) <> 'SOLICITANTE';
    SELECT RAISE(ABORT, 'RN-13: id_signatario deve apontar para uma pessoa do tipo SIGNATARIO')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_signatario) <> 'SIGNATARIO';
END;

CREATE TRIGGER trg_medicao_papeis_upd
BEFORE UPDATE OF id_equipamento, id_tecnico, id_solicitante, id_signatario ON medicao
BEGIN
    SELECT RAISE(ABORT, 'Item calibrado deve ser da categoria ITEM_CALIBRADO')
    WHERE (SELECT categoria FROM equipamento WHERE id_equipamento = NEW.id_equipamento) <> 'ITEM_CALIBRADO';
    SELECT RAISE(ABORT, 'id_tecnico deve apontar para uma pessoa do tipo TECNICO')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_tecnico) <> 'TECNICO';
    SELECT RAISE(ABORT, 'id_solicitante deve apontar para um cadastro do tipo SOLICITANTE')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_solicitante) <> 'SOLICITANTE';
    SELECT RAISE(ABORT, 'RN-13: id_signatario deve apontar para uma pessoa do tipo SIGNATARIO')
    WHERE (SELECT tipo FROM tecnico_solicitante WHERE id_pessoa = NEW.id_signatario) <> 'SIGNATARIO';
END;

-- RN-02 / RN-15: resultado gravado e imutavel (inclusive o status)
CREATE TRIGGER trg_medicao_bloqueia_alteracao
BEFORE UPDATE OF numero_certificado, parametro, valor_nominal, tolerancia_superior,
                 tolerancia_inferior, medida_1, medida_2, valor_obtido, erro,
                 desvio_padrao, incerteza_expandida, fator_k, status,
                 arquivo_origem, hash_arquivo ON medicao
BEGIN
    SELECT RAISE(ABORT, 'RN-02: valores de medicao nao podem ser alterados apos a importacao');
END;

-- RN-16 / RN-25: historico de medicao nao pode ser apagado
CREATE TRIGGER trg_medicao_bloqueia_exclusao
BEFORE DELETE ON medicao
BEGIN
    SELECT RAISE(ABORT, 'RN-25: medicoes nao podem ser excluidas; o historico deve ser preservado');
END;
