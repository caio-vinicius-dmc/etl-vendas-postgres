-- Estrutura do destino. O script e idempotente: pode rodar quantas vezes
-- quiser sem quebrar nada e sem perder dado já carregado.

CREATE SCHEMA IF NOT EXISTS stg;   -- área de pouso, sempre descartável
CREATE SCHEMA IF NOT EXISTS dw;    -- dados tratados
CREATE SCHEMA IF NOT EXISTS ctl;   -- controle do próprio ETL

-- ---------------------------------------------------------------------------
-- Staging
-- ---------------------------------------------------------------------------
-- Tudo entra como texto. A conversão acontece na etapa de transformação,
-- onde dá para tratar o erro e mandar a linha para a quarentena. Se a coluna
-- fosse tipada aqui, o COPY inteiro morreria por causa de uma linha ruim.
CREATE TABLE IF NOT EXISTS stg.vendas (
    id_venda       text,
    data_venda     text,
    cliente_id     text,
    produto_sku    text,
    quantidade     text,
    valor_unitario text,
    canal          text,
    arquivo_origem text NOT NULL,
    carregado_em   timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Dados tratados
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dw.produto (
    sku            text PRIMARY KEY,
    nome           text          NOT NULL,
    categoria      text          NOT NULL,
    preco_tabela   numeric(10,2) NOT NULL,
    ativo          boolean       NOT NULL DEFAULT true,
    atualizado_em  timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dw.venda (
    id_venda       text PRIMARY KEY,
    data_venda     date          NOT NULL,
    cliente_id     integer       NOT NULL,
    produto_sku    text          NOT NULL REFERENCES dw.produto (sku),
    quantidade     integer       NOT NULL CHECK (quantidade > 0),
    valor_unitario numeric(10,2) NOT NULL CHECK (valor_unitario >= 0),
    valor_total    numeric(12,2) NOT NULL,
    canal          text          NOT NULL,
    atualizado_em  timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_venda_data ON dw.venda (data_venda);
CREATE INDEX IF NOT EXISTS idx_venda_produto ON dw.venda (produto_sku);

-- ---------------------------------------------------------------------------
-- Controle
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ctl.execucao (
    id               bigserial PRIMARY KEY,
    pipeline         text        NOT NULL,
    janela_inicio    date        NOT NULL,
    janela_fim       date        NOT NULL,
    status           text        NOT NULL DEFAULT 'em_andamento',
    arquivos_lidos   integer     NOT NULL DEFAULT 0,
    linhas_lidas     integer     NOT NULL DEFAULT 0,
    linhas_gravadas  integer     NOT NULL DEFAULT 0,
    linhas_rejeitadas integer    NOT NULL DEFAULT 0,
    iniciado_em      timestamptz NOT NULL DEFAULT now(),
    finalizado_em    timestamptz,
    erro             text
);

-- Quarentena. Guardar a linha crua em JSONB permite reprocessar depois de
-- corrigir a regra, sem precisar voltar ao arquivo original.
CREATE TABLE IF NOT EXISTS ctl.rejeitado (
    id           bigserial PRIMARY KEY,
    execucao_id  bigint      NOT NULL REFERENCES ctl.execucao (id),
    arquivo      text        NOT NULL,
    motivo       text        NOT NULL,
    linha        jsonb       NOT NULL,
    criado_em    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rejeitado_execucao ON ctl.rejeitado (execucao_id);

-- Marca d'agua: até que data o pipeline já processou com sucesso.
-- Uma linha por pipeline.
CREATE TABLE IF NOT EXISTS ctl.marca_dagua (
    pipeline          text PRIMARY KEY,
    ultima_data       date        NOT NULL,
    atualizado_em     timestamptz NOT NULL DEFAULT now()
);
