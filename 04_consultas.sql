-- =====================================================================
-- 04_consultas.sql - Consultas de apoio (nao cria objetos no banco)
-- Execute no VS Code (extensao SQLite) ou: python consultar.py 00091/2026
-- =====================================================================

-- [certificado] Tabela "4. Resultados (mm)" do certificado
-- Arredonda somente na exibicao (RN-08); no banco a precisao e total.
SELECT
    m.numero_certificado,
    m.data_emissao,
    sol.nome                              AS solicitante,
    e.denominacao,
    e.codigo,
    COALESCE(e.fabricante, 'Não consta')  AS fabricante,
    p.codigo                              AS padrao_codigo,
    p.certificado_calibracao              AS padrao_certificado,
    p.data_proxima_calibracao             AS padrao_validade,
    m.sequencia,
    m.parametro,
    ROUND(m.valor_nominal, 3)             AS valor_nominal,
    ROUND(m.tolerancia_superior, 3)       AS tol_mais,
    ROUND(m.tolerancia_inferior, 3)       AS tol_menos,
    ROUND(m.valor_obtido, 3)              AS valor_obtido,
    ROUND(m.erro, 3)                      AS erro,
    m.incerteza_expandida,
    m.fator_k,
    m.status,
    t.nome                                AS tecnico_executante,
    sig.nome                              AS signatario
FROM medicao m
JOIN equipamento              e   ON e.id_equipamento = m.id_equipamento
JOIN equipamento              p   ON p.id_equipamento = m.id_padrao
JOIN tecnico_solicitante      t   ON t.id_pessoa      = m.id_tecnico
LEFT JOIN tecnico_solicitante sol ON sol.id_pessoa    = m.id_solicitante
LEFT JOIN tecnico_solicitante sig ON sig.id_pessoa    = m.id_signatario
WHERE m.numero_certificado = :certificado
ORDER BY m.sequencia;

-- [historico] Historico de calibracoes de um equipamento
SELECT m.numero_certificado, m.data_medicao, COUNT(*) AS parametros,
       SUM(m.status = 'REPROVADO') AS fora_tolerancia
FROM medicao m JOIN equipamento e ON e.id_equipamento = m.id_equipamento
WHERE e.codigo = :codigo
GROUP BY m.numero_certificado, m.data_medicao
ORDER BY m.data_medicao DESC;

-- [padroes] Padroes vencidos ou sem validade cadastrada
SELECT codigo, denominacao, data_proxima_calibracao
FROM equipamento
WHERE categoria = 'PADRAO'
  AND (data_proxima_calibracao IS NULL OR data_proxima_calibracao < date('now'))
ORDER BY codigo;

-- [tabelas] Confere que o banco tem somente as 3 tabelas do modelo
SELECT name FROM sqlite_master
WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
ORDER BY name;
