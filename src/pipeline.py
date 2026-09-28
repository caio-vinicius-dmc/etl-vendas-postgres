"""Orquestração das etapas.

Toda a execução acontece dentro de uma transação única: ou a janela inteira
entra, ou nada entra. O registro de controle e gravado em uma conexão
separada, para que a linha de erro sobreviva ao rollback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg.rows import dict_row

from . import banco, carga, extracao, transformacao
from .config import Config
from .log import evento

NOME_PIPELINE = "vendas_diarias"


@dataclass
class Resumo:
    execucao_id: int
    arquivos: int
    linhas_lidas: int
    gravadas: int
    rejeitadas: int
    duplicadas: int
    removidas: int


def executar(
    cfg: Config,
    logger: logging.Logger,
    inicio: date,
    fim: date,
    recarregar: bool = False,
) -> Resumo:
    arquivos = extracao.localizar_arquivos(cfg.dir_entrada, inicio, fim)
    if not arquivos:
        raise RuntimeError(
            f"Nenhum arquivo de venda entre {inicio} e {fim} em {cfg.dir_entrada}. "
            "Rode 'python -m src.cli gerar-fontes' se ainda não gerou a origem."
        )

    # Conexão dedicada ao controle: ela commita o registro de execução
    # independentemente do que acontecer com a transação dos dados.
    with psycopg.connect(cfg.dsn, row_factory=dict_row, autocommit=True) as ctl:
        execucao_id = banco.abrir_execucao(ctl, NOME_PIPELINE, inicio, fim)
        evento(
            logger,
            "Execução aberta",
            execucao=execucao_id,
            janela=f"{inicio}..{fim}",
            arquivos=len(arquivos),
        )

        try:
            resumo = _processar(cfg, logger, execucao_id, arquivos, inicio, fim, recarregar)
        except Exception as erro:
            banco.fechar_execucao(ctl, execucao_id, "erro", erro=str(erro))
            evento(logger, "Execução abortada", execucao=execucao_id, erro=str(erro))
            raise

        banco.fechar_execucao(
            ctl,
            execucao_id,
            "concluida",
            arquivos=resumo.arquivos,
            lidas=resumo.linhas_lidas,
            gravadas=resumo.gravadas,
            rejeitadas=resumo.rejeitadas,
        )

    return resumo


def _processar(
    cfg: Config,
    logger: logging.Logger,
    execucao_id: int,
    arquivos: list[extracao.ArquivoVenda],
    inicio: date,
    fim: date,
    recarregar: bool,
) -> Resumo:
    with banco.conectar(cfg) as conn:
        produtos = extracao.ler_catalogo(cfg.dir_entrada / "catalogo_produtos.json")
        carga.carregar_produtos(conn, produtos)
        skus_validos = {p["sku"] for p in produtos}
        evento(logger, "Catálogo carregado", produtos=len(produtos))

        extracao.limpar_staging(conn)
        lidas = extracao.carregar_para_staging(conn, arquivos)
        evento(logger, "Staging carregado", linhas=lidas, arquivos=len(arquivos))

        linhas = conn.execute(
            "SELECT * FROM stg.vendas"
        ).fetchall()

        validas, rejeitadas = transformacao.separar(linhas, skus_validos)
        validas, duplicadas = transformacao.remover_duplicatas(validas)
        evento(
            logger,
            "Validação concluída",
            validas=len(validas),
            rejeitadas=len(rejeitadas),
            duplicadas=duplicadas,
        )

        removidas = 0
        if recarregar:
            removidas = carga.limpar_janela(conn, inicio, fim)
            evento(logger, "Janela limpa antes da recarga", removidas=removidas)

        gravadas = carga.carregar_vendas(conn, validas)
        carga.registrar_rejeitados(conn, execucao_id, rejeitadas)
        banco.atualizar_marca_dagua(conn, NOME_PIPELINE, fim)

        evento(logger, "Carga concluída", gravadas=gravadas)

    return Resumo(
        execucao_id=execucao_id,
        arquivos=len(arquivos),
        linhas_lidas=lidas,
        gravadas=gravadas,
        rejeitadas=len(rejeitadas),
        duplicadas=duplicadas,
        removidas=removidas,
    )


def janela_incremental(cfg: Config, ate: date) -> tuple[date, date]:
    """Calcula a janela a partir da marca d'água.

    Se a pipeline nunca rodou, processa tudo o que existir na pasta de
    entrada. Se já rodou, começa no dia seguinte ao último processado.
    """
    from datetime import timedelta

    with banco.conectar(cfg) as conn:
        ultima = banco.ler_marca_dagua(conn, NOME_PIPELINE)

    if ultima is None:
        arquivos = sorted(cfg.dir_entrada.glob("vendas_*.csv"))
        if not arquivos:
            raise RuntimeError(
                f"Nenhum arquivo de venda em {cfg.dir_entrada}. "
                "Rode 'python -m src.cli gerar-fontes' primeiro."
            )
        primeira = date.fromisoformat(arquivos[0].stem.removeprefix("vendas_"))
        return primeira, ate

    return ultima + timedelta(days=1), ate
