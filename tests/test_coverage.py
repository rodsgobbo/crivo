"""Verifica mecanicamente o mapa de cobertura.

O mapa em `coverage_map.py` e julgamento humano: alguem decidiu que determinado
teste verifica determinado criterio. Este arquivo nao consegue julgar isso, e
nao tenta. O que ele consegue e impedir que o mapa apodreca em silencio.

Duas formas de apodrecimento sao pegas aqui. Um criterio novo entra nos
requisitos e ninguem mapeia; um teste e renomeado ou removido e a entrada do
mapa passa a apontar para o nada. Sem esta verificacao, as duas coisas
aconteceriam sem ruido e o mapa viraria decoracao.
"""

from __future__ import annotations

import re
from pathlib import Path

from coverage_map import COVERAGE

RAIZ = Path(__file__).resolve().parents[1]
REQUISITOS = RAIZ / ".specs" / "changes" / "linkedin-job-agent" / "requirements.md"
TESTES = Path(__file__).parent


def criterios_declarados() -> set[str]:
    texto = REQUISITOS.read_text(encoding="utf-8")
    return set(re.findall(r"^(\d+\.\d+) ", texto, re.M))


def funcoes_existentes() -> set[str]:
    encontradas = set()
    for arquivo in TESTES.glob("test_*.py"):
        for nome in re.findall(
            r"^def (test_\w+)", arquivo.read_text(encoding="utf-8"), re.M
        ):
            encontradas.add(f"{arquivo.stem}::{nome}")
    return encontradas


def test_every_acceptance_criterion_is_mapped():
    faltando = sorted(
        criterios_declarados() - set(COVERAGE),
        key=lambda c: tuple(int(p) for p in c.split(".")),
    )
    assert not faltando, f"criterios sem teste mapeado: {faltando}"


def test_the_map_has_no_entry_for_an_unknown_criterion():
    sobrando = sorted(set(COVERAGE) - criterios_declarados())
    assert not sobrando, f"mapa cita criterios inexistentes: {sobrando}"


def test_every_mapped_test_exists():
    existentes = funcoes_existentes()
    quebradas = sorted(
        {
            f"{criterio} -> {alvo}"
            for criterio, alvos in COVERAGE.items()
            for alvo in alvos
            if alvo not in existentes
        }
    )
    assert not quebradas, f"mapa aponta para testes inexistentes: {quebradas}"


def test_no_criterion_is_mapped_to_an_empty_list():
    vazios = sorted(c for c, alvos in COVERAGE.items() if not alvos)
    assert not vazios, f"criterios mapeados para lista vazia: {vazios}"
