"""Redacao de segredos, testemunhos e cookies em registros de log.

O filtro e instalado na raiz do sistema de log durante a inicializacao. A
consequencia pretendida e que um vazamento em log passe a ser um defeito
localizado neste modulo, em vez de um descuido possivel em cada chamada que
formata uma mensagem.

Duas estrategias operam juntas. A primeira apaga os valores exatos que o cofre
ja entregou, e cobre o segredo do operador. A segunda apaga por forma o que o
cofre nunca vera: testemunhos de acesso de terceiros, credenciais de provedor
informadas por usuarios e cabecalhos de cookie.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[redigido]"

#: Formatador auxiliar usado apenas para materializar tracebacks antes da redacao.
_FORMATTER = logging.Formatter()

#: Padroes de material sensivel que nao passa pelo cofre do operador.
PATTERNS: tuple[re.Pattern[str], ...] = (
    # Cabecalho de autorizacao, com ou sem esquema nomeado.
    re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer\s+)?\S+"),
    # Cabecalho de cookie e atribuicao de cookie de sessao.
    re.compile(r"(?i)\b(set-cookie\s*[:=]\s*|cookie\s*[:=]\s*)\S+"),
    # Atribuicao explicita de material sensivel em texto livre ou mapa.
    re.compile(
        r"(?i)\b((?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
        r"client[_-]?secret|password|senha|secret|token)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s,;)}\]\"']+"
    ),
    # Formato de testemunho assinado em tres segmentos.
    re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b"),
)


def redact(text: str, literals: Iterable[str] = ()) -> str:
    """Devolve `text` com valores literais e material por forma substituidos."""
    if not text:
        return text
    # Os literais mais longos primeiro: um segredo curto contido em outro maior
    # nao pode consumir parte dele e deixar um fragmento legivel para tras.
    for literal in sorted({v for v in literals if v}, key=len, reverse=True):
        text = text.replace(literal, REDACTED)
    for pattern in PATTERNS:
        text = pattern.sub(
            lambda m: (m.group(1) if m.lastindex else "") + REDACTED, text
        )
    return text


class RedactionFilter(logging.Filter):
    """Reescreve a mensagem e os argumentos de cada registro antes da emissao."""

    def __init__(self, vault=None) -> None:
        super().__init__()
        self._vault = vault

    def _literals(self) -> frozenset[str]:
        if self._vault is None:
            return frozenset()
        return self._vault.sensitive_values

    def filter(self, record: logging.LogRecord) -> bool:
        literals = self._literals()
        # A mensagem e interpolada aqui para que valores sensiveis presentes nos
        # argumentos nao escapem pela formatacao posterior do handler.
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)
        record.msg = redact(rendered, literals)
        record.args = ()
        # O traceback so seria formatado adiante, pelo handler, e por isso
        # `exc_text` chega vazio aqui. Formatamos agora para redigir, e o
        # formatador a jusante reaproveita `exc_text` em vez de refazer.
        if record.exc_info and not record.exc_text:
            record.exc_text = _FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, literals)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, literals)
        return True


def install(vault=None, logger: logging.Logger | None = None) -> RedactionFilter:
    """Instala o filtro na raiz do log e devolve a instancia instalada."""
    target = logging.getLogger() if logger is None else logger
    for existing in target.filters:
        if isinstance(existing, RedactionFilter):
            target.removeFilter(existing)
    installed = RedactionFilter(vault)
    target.addFilter(installed)
    for handler in target.handlers:
        handler.addFilter(installed)
    return installed
