"""Carga: grava produtos e vendas no destino de forma idempotente."""

from __future__ import annotations

import json

import psycopg

from .transformacao import Rejeicao, Venda


def carregar_produtos(conn: psycopg.Connection, produtos: list[dict]) -> int:
    """Upsert do catálogo.

    O catálogo chega sempre completo (snapshot), então o ON CONFLICT
    atualiza quem já existe e insere quem e novo. Produtos que sumiram do
    arquivo não são apagados de propósito: venda antiga ainda aponta para
    eles e a FK quebraria.
    """
    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO dw.produto (sku, nome, categoria, preco_tabela, ativo)
            VALUES (%(sku)s, %(nome)s, %(categoria)s, %(preco_tabela)s, %(ativo)s)
            ON CONFLICT (sku) DO UPDATE
                SET nome          = EXCLUDED.nome,
                    categoria     = EXCLUDED.categoria,
                    preco_tabela  = EXCLUDED.preco_tabela,
                    ativo         = EXCLUDED.ativo,
                    atualizado_em = now()
            """,
            produtos,
        )
    return len(produtos)


def carregar_vendas(conn: psycopg.Connection, vendas: list[Venda]) -> int:
    """Upsert das vendas pela chave natural id_venda.

    E o que torna a pipeline segura para rodar duas vezes na mesma janela:
    a segunda execução atualiza as mesmas linhas em vez de duplicar.
    """
    if not vendas:
        return 0

    registros = [
        (
            v.id_venda,
            v.data_venda,
            v.cliente_id,
            v.produto_sku,
            v.quantidade,
            v.valor_unitario,
            v.valor_total,
            v.canal,
        )
        for v in vendas
    ]

    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO dw.venda (
                id_venda, data_venda, cliente_id, produto_sku,
                quantidade, valor_unitario, valor_total, canal
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id_venda) DO UPDATE
                SET data_venda     = EXCLUDED.data_venda,
                    cliente_id     = EXCLUDED.cliente_id,
                    produto_sku    = EXCLUDED.produto_sku,
                    quantidade     = EXCLUDED.quantidade,
                    valor_unitario = EXCLUDED.valor_unitario,
                    valor_total    = EXCLUDED.valor_total,
                    canal          = EXCLUDED.canal,
                    atualizado_em  = now()
            """,
            registros,
        )

    return len(registros)


def registrar_rejeitados(
    conn: psycopg.Connection, execucao_id: int, rejeicoes: list[Rejeicao]
) -> int:
    if not rejeicoes:
        return 0

    registros = [
        (execucao_id, r.arquivo, r.motivo, json.dumps(r.linha, ensure_ascii=False))
        for r in rejeicoes
    ]

    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO ctl.rejeitado (execucao_id, arquivo, motivo, linha)
            VALUES (%s, %s, %s, %s)
            """,
            registros,
        )

    return len(registros)


def limpar_janela(conn: psycopg.Connection, inicio, fim) -> int:
    """Apaga as vendas da janela antes de recarregar.

    Usado apenas no modo --recarregar. Serve para o caso em que a origem
    deixou de enviar uma venda que antes existia: o upsert sozinho nunca
    removeria essa linha órfã.
    """
    resultado = conn.execute(
        "DELETE FROM dw.venda WHERE data_venda BETWEEN %s AND %s", (inicio, fim)
    )
    return resultado.rowcount
