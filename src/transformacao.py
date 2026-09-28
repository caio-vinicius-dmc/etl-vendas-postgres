"""Transformação: valida, converte tipos e separa o que não passa.

A regra aqui e nunca deixar uma linha ruim derrubar a execução inteira.
Cada linha passa pelas validações e sai por um de dois caminhos: virar um
registro valido ou ir para a quarentena com o motivo anotado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

CANAIS_ACEITOS = {"loja", "site", "marketplace", "app"}

# Colunas que vieram do arquivo. O staging tem outras duas de controle
# (arquivo_origem e carregado_em) que não fazem parte do dado original e
# não devem ir para a quarentena.
COLUNAS_DO_ARQUIVO = (
    "id_venda",
    "data_venda",
    "cliente_id",
    "produto_sku",
    "quantidade",
    "valor_unitario",
    "canal",
)


@dataclass(frozen=True)
class Venda:
    id_venda: str
    data_venda: date
    cliente_id: int
    produto_sku: str
    quantidade: int
    valor_unitario: Decimal
    valor_total: Decimal
    canal: str


@dataclass(frozen=True)
class Rejeicao:
    motivo: str
    linha: dict
    arquivo: str


def _texto(valor: object) -> str:
    return (valor or "").strip() if isinstance(valor, str) else ""


def validar(linha: dict, skus_validos: set[str]) -> Venda | Rejeicao:
    arquivo = linha.get("arquivo_origem", "?")

    def rejeitar(motivo: str) -> Rejeicao:
        # So os campos originais do arquivo vão para a quarentena, para que a
        # linha guardada possa ser reprocessada como se tivesse acabado de chegar.
        bruta = {coluna: linha.get(coluna) for coluna in COLUNAS_DO_ARQUIVO}
        return Rejeicao(motivo=motivo, linha=bruta, arquivo=arquivo)

    id_venda = _texto(linha.get("id_venda"))
    if not id_venda:
        return rejeitar("id_venda ausente")

    try:
        data_venda = date.fromisoformat(_texto(linha.get("data_venda")))
    except ValueError:
        return rejeitar("data_venda fora do formato AAAA-MM-DD ou inexistente")

    cliente_bruto = _texto(linha.get("cliente_id"))
    if not cliente_bruto.isdigit():
        return rejeitar("cliente_id ausente ou não numérico")
    cliente_id = int(cliente_bruto)

    sku = _texto(linha.get("produto_sku"))
    if sku not in skus_validos:
        # Integridade referencial conferida antes de chegar no banco:
        # o erro fica legível em vez de virar uma violação de FK.
        return rejeitar(f"produto {sku or '(vazio)'} não existe no catálogo")

    quantidade_bruta = _texto(linha.get("quantidade"))
    if not quantidade_bruta.lstrip("-").isdigit():
        return rejeitar("quantidade não numérica")
    quantidade = int(quantidade_bruta)
    if quantidade <= 0:
        return rejeitar("quantidade precisa ser maior que zero")

    try:
        valor_unitario = Decimal(_texto(linha.get("valor_unitario")))
    except InvalidOperation:
        return rejeitar("valor_unitario não numérico")
    if valor_unitario < 0:
        return rejeitar("valor_unitario negativo")

    canal = _texto(linha.get("canal")).lower()
    if canal not in CANAIS_ACEITOS:
        return rejeitar(f"canal '{canal}' fora do domínio aceito")

    return Venda(
        id_venda=id_venda,
        data_venda=data_venda,
        cliente_id=cliente_id,
        produto_sku=sku,
        quantidade=quantidade,
        valor_unitario=valor_unitario,
        # Calculado aqui, e não no banco, para que o valor gravado seja
        # exatamente o que a regra de negocio definiu.
        valor_total=(valor_unitario * quantidade).quantize(Decimal("0.01")),
        canal=canal,
    )


def separar(
    linhas: list[dict], skus_validos: set[str]
) -> tuple[list[Venda], list[Rejeicao]]:
    validas: list[Venda] = []
    rejeitadas: list[Rejeicao] = []

    for linha in linhas:
        resultado = validar(linha, skus_validos)
        if isinstance(resultado, Venda):
            validas.append(resultado)
        else:
            rejeitadas.append(resultado)

    return validas, rejeitadas


def remover_duplicatas(vendas: list[Venda]) -> tuple[list[Venda], int]:
    """Mantem a última ocorrência de cada id_venda dentro do lote.

    O mesmo id pode aparecer duas vezes quando o sistema de origem reenvia
    um arquivo corrigido. Sem essa limpeza o ON CONFLICT do carregamento
    falharia com 'cannot affect row a second time'.
    """
    por_id: dict[str, Venda] = {}
    for venda in vendas:
        por_id[venda.id_venda] = venda
    return list(por_id.values()), len(vendas) - len(por_id)
