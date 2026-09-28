"""Extração: localiza os arquivos da janela e joga o conteudo no staging."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

PADRAO_ARQUIVO_VENDAS = re.compile(r"^vendas_(\d{4}-\d{2}-\d{2})\.csv$")

COLUNAS_VENDA = [
    "id_venda",
    "data_venda",
    "cliente_id",
    "produto_sku",
    "quantidade",
    "valor_unitario",
    "canal",
]


@dataclass(frozen=True)
class ArquivoVenda:
    caminho: Path
    data: date


def localizar_arquivos(
    diretorio: Path, inicio: date, fim: date
) -> list[ArquivoVenda]:
    """Seleciona os arquivos cuja data no nome cai dentro da janela.

    Filtrar pelo nome em vez de abrir tudo evita ler arquivo que não
    interessa -- em uma pasta com um ano de histórico isso pesa.
    """
    encontrados: list[ArquivoVenda] = []

    for caminho in sorted(diretorio.glob("vendas_*.csv")):
        casamento = PADRAO_ARQUIVO_VENDAS.match(caminho.name)
        if not casamento:
            continue
        try:
            data_arquivo = date.fromisoformat(casamento.group(1))
        except ValueError:
            continue
        if inicio <= data_arquivo <= fim:
            encontrados.append(ArquivoVenda(caminho=caminho, data=data_arquivo))

    return encontrados


def limpar_staging(conn: psycopg.Connection) -> None:
    """O staging é sempre reconstruido do zero na execução.

    TRUNCATE em vez de DELETE porque não há nada a preservar e o TRUNCATE
    devolve o espaço imediatamente.
    """
    conn.execute("TRUNCATE stg.vendas")


def carregar_para_staging(
    conn: psycopg.Connection, arquivos: list[ArquivoVenda]
) -> int:
    """Le os CSVs e manda para o staging usando COPY.

    COPY é uma ordem de grandeza mais rápido que INSERT linha a linha, e
    como toda coluna do staging e texto nenhuma linha malformada derruba a
    carga -- o problema aparece depois, na validação.
    """
    total = 0
    destino = "COPY stg.vendas ({}, arquivo_origem) FROM STDIN".format(
        ", ".join(COLUNAS_VENDA)
    )

    with conn.cursor().copy(destino) as copia:
        for arquivo in arquivos:
            with arquivo.caminho.open(encoding="utf-8", newline="") as entrada:
                leitor = csv.DictReader(entrada, delimiter=";")
                for linha in leitor:
                    copia.write_row(
                        [linha.get(coluna) for coluna in COLUNAS_VENDA]
                        + [arquivo.caminho.name]
                    )
                    total += 1

    return total


def ler_catalogo(caminho: Path) -> list[dict]:
    if not caminho.exists():
        raise FileNotFoundError(
            f"Catálogo de produtos não encontrado em {caminho}. "
            "Rode 'python -m src.cli gerar-fontes' antes."
        )
    return json.loads(caminho.read_text(encoding="utf-8"))
