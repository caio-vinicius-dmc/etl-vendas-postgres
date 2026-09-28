"""Interface de linha de comando da pipeline.

    python -m src.cli gerar-fontes --dias 30
    python -m src.cli migrar
    python -m src.cli executar
    python -m src.cli executar --de 2026-01-05 --ate 2026-01-10 --recarregar
    python -m src.cli status
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from rich.console import Console
from rich.table import Table

from . import banco, config, gerador_fontes, log, pipeline

console = Console()


def _br(valor, casas: int = 0) -> str:
    """Formata número no padrão brasileiro: ponto no milhar, vírgula no decimal.

    O Python não tem locale pt-BR garantido em toda máquina, então a troca é
    feita na mão usando um marcador temporário para não embaralhar os
    separadores no meio do caminho.
    """
    bruto = f"{valor:,.{casas}f}"
    return bruto.replace(",", "@").replace(".", ",").replace("@", ".")


def _data(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{texto}' não é uma data válida. Use o formato AAAA-MM-DD."
        )


def comando_gerar_fontes(args: argparse.Namespace) -> int:
    cfg = config.carregar()
    cfg.dir_entrada.mkdir(parents=True, exist_ok=True)

    inicio = args.de or (date.today() - timedelta(days=args.dias - 1))
    catalogo = gerador_fontes.gerar_catalogo(cfg.dir_entrada)
    arquivos = gerador_fontes.gerar_vendas(
        cfg.dir_entrada, inicio, args.dias, vendas_por_dia=args.por_dia
    )

    console.print(f"Catálogo: {catalogo.name}")
    console.print(
        f"{len(arquivos)} arquivos de venda, de {arquivos[0].name} a {arquivos[-1].name}"
    )
    console.print(
        f"Cerca de {gerador_fontes.TAXA_DEFEITO:.0%} das linhas saem com defeito "
        "de propósito, para exercitar a quarentena."
    )
    return 0


def comando_migrar(args: argparse.Namespace) -> int:
    cfg = config.carregar()
    console.print(f"Conectando em {cfg.destino_legivel()}...")
    banco.esperar_banco(cfg)
    for script in banco.migrar(cfg):
        console.print(f"  aplicado: {script}")
    console.print("Estrutura em dia.")
    return 0


def comando_executar(args: argparse.Namespace) -> int:
    cfg = config.carregar()
    logger = log.configurar(cfg.formato_log)
    banco.esperar_banco(cfg)

    ate = args.ate or date.today()
    if args.de:
        inicio, fim = args.de, ate
    else:
        inicio, fim = pipeline.janela_incremental(cfg, ate)

    if inicio > fim:
        console.print(
            f"Nada a fazer: a marca d'água já está em {inicio - timedelta(days=1)}."
        )
        return 0

    resumo = pipeline.executar(cfg, logger, inicio, fim, recarregar=args.recarregar)

    tabela = Table(title=f"Execução {resumo.execucao_id}: {inicio} a {fim}")
    tabela.add_column("Métrica")
    tabela.add_column("Valor", justify="right")
    tabela.add_row("Arquivos lidos", _br(resumo.arquivos))
    tabela.add_row("Linhas lidas", _br(resumo.linhas_lidas))
    tabela.add_row("Gravadas", _br(resumo.gravadas))
    tabela.add_row("Rejeitadas", _br(resumo.rejeitadas))
    tabela.add_row("Duplicadas no lote", _br(resumo.duplicadas))
    if resumo.removidas:
        tabela.add_row("Removidas na recarga", _br(resumo.removidas))
    console.print(tabela)
    return 0


# O status fica gravado sem acento -- é o valor que o código compara e o que
# a coluna guarda. O que aparece na tela vai em português corrente.
def _quando(instante) -> str:
    """Escreve o instante no fuso de quem está lendo.

    A coluna é `timestamptz`, então o banco guarda o instante certo. Mas o
    container roda em UTC: formatar lá dentro faria quem executa às 18h ver
    21h no relatório, e o horário é a primeira coisa que se olha para saber
    se a carga rodou. O `astimezone()` sem argumento usa o fuso do sistema
    operacional, que é exatamente o de quem está na frente da tela.
    """
    if instante is None:
        return "-"
    return instante.astimezone().strftime("%d/%m %H:%M")


ROTULO_STATUS = {
    "em_andamento": "em andamento",
    "concluida": "concluída",
    "erro": "erro",
}


def comando_status(args: argparse.Namespace) -> int:
    cfg = config.carregar()
    banco.esperar_banco(cfg)

    with banco.conectar(cfg) as conn:
        marca = banco.ler_marca_dagua(conn, pipeline.NOME_PIPELINE)
        execucoes = conn.execute(
            """
            SELECT id, janela_inicio, janela_fim, status,
                   linhas_lidas, linhas_gravadas, linhas_rejeitadas,
                   iniciado_em
              FROM ctl.execucao
             ORDER BY id DESC
             LIMIT %s
            """,
            (args.limite,),
        ).fetchall()
        motivos = conn.execute(
            """
            SELECT motivo, count(*) AS total
              FROM ctl.rejeitado
             GROUP BY motivo
             ORDER BY total DESC
            """
        ).fetchall()
        total_vendas = conn.execute(
            "SELECT count(*) AS total, coalesce(sum(valor_total), 0) AS faturamento "
            "FROM dw.venda"
        ).fetchone()

    console.print(f"Marca d'água: {marca or 'ainda não definida'}")
    console.print(
        f"Vendas no destino: {_br(total_vendas['total'])} | "
        f"faturamento acumulado: R$ {_br(total_vendas['faturamento'], 2)}"
    )

    if execucoes:
        tabela = Table(title="Últimas execuções")
        for coluna in ("#", "Quando", "Janela", "Status", "Lidas", "Gravadas", "Rejeitadas"):
            tabela.add_column(coluna)
        for e in execucoes:
            tabela.add_row(
                str(e["id"]),
                _quando(e["iniciado_em"]),
                f"{e['janela_inicio']} a {e['janela_fim']}",
                ROTULO_STATUS.get(e["status"], e["status"]),
                _br(e["linhas_lidas"]),
                _br(e["linhas_gravadas"]),
                _br(e["linhas_rejeitadas"]),
            )
        console.print(tabela)

    if motivos:
        tabela = Table(title="Motivos de rejeição acumulados")
        tabela.add_column("Motivo")
        tabela.add_column("Linhas", justify="right")
        for m in motivos:
            tabela.add_row(m["motivo"], _br(m["total"]))
        console.print(tabela)

    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etl-vendas",
        description="Pipeline de ingestão de vendas em arquivo para o PostgreSQL.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_fontes = sub.add_parser("gerar-fontes", help="cria os arquivos de origem")
    p_fontes.add_argument("--dias", type=int, default=30, help="quantos dias gerar")
    p_fontes.add_argument("--de", type=_data, help="primeiro dia (padrão: hoje - dias)")
    p_fontes.add_argument("--por-dia", type=int, default=400, help="vendas por dia")
    p_fontes.set_defaults(funcao=comando_gerar_fontes)

    p_migrar = sub.add_parser("migrar", help="cria ou atualiza a estrutura no banco")
    p_migrar.set_defaults(funcao=comando_migrar)

    p_exec = sub.add_parser("executar", help="roda a pipeline")
    p_exec.add_argument("--de", type=_data, help="início da janela; sem isso usa a marca d'água")
    p_exec.add_argument("--ate", type=_data, help="fim da janela (padrão: hoje)")
    p_exec.add_argument(
        "--recarregar",
        action="store_true",
        help="apaga as vendas da janela antes de gravar, em vez de só atualizar",
    )
    p_exec.set_defaults(funcao=comando_executar)

    p_status = sub.add_parser("status", help="mostra marca d'água, execuções e rejeições")
    p_status.add_argument("--limite", type=int, default=10)
    p_status.set_defaults(funcao=comando_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return args.funcao(args)
    except (RuntimeError, FileNotFoundError) as erro:
        console.print(f"[red]{erro}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
