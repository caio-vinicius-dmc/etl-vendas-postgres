"""Log da pipeline.

São dois formatos porque o uso é diferente: no terminal a gente quer ler,
em produção um coletor quer campos. O código da pipeline chama sempre o
mesmo método e não precisa saber qual formato está ativo.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class FormatadorJson(logging.Formatter):
    def format(self, registro: logging.LogRecord) -> str:
        evento: dict[str, Any] = {
            "momento": self.formatTime(registro, "%Y-%m-%dT%H:%M:%S"),
            "nivel": registro.levelname.lower(),
            "mensagem": registro.getMessage(),
        }
        # Campos extras passados via logger.info("...", extra={"contexto": {...}})
        contexto = getattr(registro, "contexto", None)
        if contexto:
            evento.update(contexto)
        if registro.exc_info:
            evento["excecao"] = self.formatException(registro.exc_info)
        return json.dumps(evento, ensure_ascii=False)


class FormatadorTexto(logging.Formatter):
    def format(self, registro: logging.LogRecord) -> str:
        base = f"{self.formatTime(registro, '%H:%M:%S')} {registro.levelname:<7} {registro.getMessage()}"
        contexto = getattr(registro, "contexto", None)
        if contexto:
            detalhes = " ".join(f"{k}={v}" for k, v in contexto.items())
            base = f"{base}  [{detalhes}]"
        if registro.exc_info:
            base = f"{base}\n{self.formatException(registro.exc_info)}"
        return base


def configurar(formato: str = "text") -> logging.Logger:
    logger = logging.getLogger("etl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(FormatadorJson() if formato == "json" else FormatadorTexto())
    logger.addHandler(saida)
    logger.propagate = False
    return logger


def evento(logger: logging.Logger, mensagem: str, **campos: Any) -> None:
    """Atalho para não repetir o dicionário 'extra' em cada chamada."""
    logger.info(mensagem, extra={"contexto": campos})
