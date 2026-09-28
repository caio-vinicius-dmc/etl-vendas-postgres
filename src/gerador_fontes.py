"""Cria os arquivos de origem que a pipeline consome.

Num cenário real esses arquivos chegariam de um sistema transacional ou de
um SFTP. Aqui são gerados localmente, com semente fixa para que o resultado
da pipeline seja sempre o mesmo -- inclusive a quantidade de linhas ruins.

De propósito, uma fração das linhas sai com defeito. E o que faz a etapa de
validação e a quarentena terem o que fazer.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

CATEGORIAS = ["Eletronicos", "Livros", "Moda", "Casa", "Esporte", "Games"]
CANAIS = ["loja", "site", "marketplace", "app"]

# Proporção de linhas defeituosas injetadas de propósito.
TAXA_DEFEITO = 0.03


def gerar_catalogo(destino: Path, quantidade: int = 120) -> Path:
    aleatorio = random.Random(7)
    produtos = []
    for i in range(1, quantidade + 1):
        categoria = CATEGORIAS[i % len(CATEGORIAS)]
        produtos.append(
            {
                "sku": f"SKU-{i:04d}",
                "nome": f"{categoria} modelo {i}",
                "categoria": categoria,
                "preco_tabela": round(aleatorio.uniform(19.9, 899.0), 2),
                "ativo": i % 37 != 0,  # alguns produtos descontinuados
            }
        )

    arquivo = destino / "catalogo_produtos.json"
    arquivo.write_text(
        json.dumps(produtos, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return arquivo


def _linha_defeituosa(aleatorio: random.Random, linha: dict) -> dict:
    """Estraga um campo da linha, sorteando entre os defeitos mais comuns."""
    defeito = aleatorio.choice(
        ["data", "quantidade", "valor", "sku", "vazio", "canal"]
    )
    estragada = dict(linha)
    if defeito == "data":
        estragada["data_venda"] = "31/02/2026"
    elif defeito == "quantidade":
        estragada["quantidade"] = "0"
    elif defeito == "valor":
        estragada["valor_unitario"] = "R$ 1.234,00"
    elif defeito == "sku":
        estragada["produto_sku"] = "SKU-9999"  # não existe no catálogo
    elif defeito == "vazio":
        estragada["cliente_id"] = ""
    else:
        estragada["canal"] = "televendas"  # canal fora do domínio aceito
    return estragada


def gerar_vendas(
    destino: Path,
    data_inicial: date,
    dias: int,
    vendas_por_dia: int = 400,
    qtd_produtos: int = 120,
) -> list[Path]:
    aleatorio = random.Random(13)
    arquivos: list[Path] = []
    sequencia = 0

    for deslocamento in range(dias):
        dia = data_inicial + timedelta(days=deslocamento)
        arquivo = destino / f"vendas_{dia.isoformat()}.csv"

        with arquivo.open("w", newline="", encoding="utf-8") as saida:
            escritor = csv.DictWriter(
                saida,
                fieldnames=[
                    "id_venda",
                    "data_venda",
                    "cliente_id",
                    "produto_sku",
                    "quantidade",
                    "valor_unitario",
                    "canal",
                ],
                delimiter=";",
            )
            escritor.writeheader()

            for _ in range(vendas_por_dia):
                sequencia += 1
                linha = {
                    "id_venda": f"V{sequencia:08d}",
                    "data_venda": dia.isoformat(),
                    "cliente_id": str(aleatorio.randint(1, 5000)),
                    "produto_sku": f"SKU-{aleatorio.randint(1, qtd_produtos):04d}",
                    "quantidade": str(aleatorio.randint(1, 5)),
                    "valor_unitario": f"{aleatorio.uniform(19.9, 899.0):.2f}",
                    "canal": aleatorio.choice(CANAIS),
                }
                if aleatorio.random() < TAXA_DEFEITO:
                    linha = _linha_defeituosa(aleatorio, linha)
                escritor.writerow(linha)

        arquivos.append(arquivo)

    return arquivos
