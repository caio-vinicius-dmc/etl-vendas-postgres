"""Acesso ao Postgres: conexão, migração e controle de execuções."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import date
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import RAIZ, Config


@contextmanager
def conectar(cfg: Config) -> Iterator[psycopg.Connection]:
    with psycopg.connect(cfg.dsn, row_factory=dict_row) as conn:
        yield conn


def esperar_banco(cfg: Config, tentativas: int = 30, intervalo: float = 2.0) -> None:
    ultimo_erro: Exception | None = None
    for _ in range(tentativas):
        try:
            with psycopg.connect(cfg.dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as erro:
            # Senha recusada não melhora com nova tentativa -- insistir só
            # faz o comando demorar um minuto para dar a mensagem errada.
            #
            # O caso clássico e o volume do Docker ter sido criado com outra
            # senha: o Postgres só lê POSTGRES_PASSWORD na primeira
            # inicialização do diretório de dados e ignora a mudança no .env
            # depois disso.
            if "password authentication failed" in str(erro):
                raise RuntimeError(
                    f"O banco recusou a senha de {cfg.destino_legivel()}. "
                    "Se você mudou POSTGRES_PASSWORD depois de já ter subido o "
                    "container, o volume antigo ainda guarda a senha original. "
                    "Recrie o ambiente com: docker compose down -v && docker compose up -d"
                ) from erro

            ultimo_erro = erro
            time.sleep(intervalo)
    raise RuntimeError(
        f"Sem conexão com {cfg.destino_legivel()}. "
        f"O container subiu? Último erro: {ultimo_erro}"
    )


def migrar(cfg: Config) -> list[str]:
    """Aplica os scripts de sql/ em ordem alfabetica.

    Os scripts usam IF NOT EXISTS, então reaplicar e inofensivo. Para um
    projeto maior valeria um controle de versão de schema de verdade
    (Alembic, Flyway); aqui isso seria peso sem beneficio.
    """
    aplicados = []
    with conectar(cfg) as conn:
        for script in sorted((RAIZ / "sql").glob("*.sql")):
            conn.execute(script.read_text(encoding="utf-8"))
            aplicados.append(script.name)
    return aplicados


def abrir_execucao(
    conn: psycopg.Connection, pipeline: str, inicio: date, fim: date
) -> int:
    linha = conn.execute(
        """
        INSERT INTO ctl.execucao (pipeline, janela_inicio, janela_fim)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (pipeline, inicio, fim),
    ).fetchone()
    return linha["id"]


def fechar_execucao(
    conn: psycopg.Connection,
    execucao_id: int,
    status: str,
    arquivos: int = 0,
    lidas: int = 0,
    gravadas: int = 0,
    rejeitadas: int = 0,
    erro: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ctl.execucao
           SET status = %s,
               arquivos_lidos = %s,
               linhas_lidas = %s,
               linhas_gravadas = %s,
               linhas_rejeitadas = %s,
               erro = %s,
               finalizado_em = now()
         WHERE id = %s
        """,
        (status, arquivos, lidas, gravadas, rejeitadas, erro, execucao_id),
    )


def atualizar_marca_dagua(
    conn: psycopg.Connection, pipeline: str, ate: date
) -> None:
    """Avança a marca d'água, nunca retrocede.

    O GREATEST protege o caso de reprocessar uma janela antiga: reprocessar
    marco não pode fazer a pipeline achar que abril ainda não foi carregado.
    """
    conn.execute(
        """
        INSERT INTO ctl.marca_dagua (pipeline, ultima_data)
        VALUES (%s, %s)
        ON CONFLICT (pipeline) DO UPDATE
            SET ultima_data = GREATEST(ctl.marca_dagua.ultima_data, EXCLUDED.ultima_data),
                atualizado_em = now()
        """,
        (pipeline, ate),
    )


def ler_marca_dagua(conn: psycopg.Connection, pipeline: str) -> date | None:
    linha = conn.execute(
        "SELECT ultima_data FROM ctl.marca_dagua WHERE pipeline = %s", (pipeline,)
    ).fetchone()
    return linha["ultima_data"] if linha else None
