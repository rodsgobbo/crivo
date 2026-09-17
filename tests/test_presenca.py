"""Leitura da exigencia de presenca na descricao da vaga."""

from __future__ import annotations

import pytest

from crivo.pipeline.presenca import ler


@pytest.mark.parametrize(
    "texto,dias",
    [
        ("Modelo hibrido, 3 dias por semana no escritorio.", 3),
        ("Trabalho híbrido: 2x na semana presencial.", 2),
        ("Hybrid role, 4 days a week in the office.", 4),
        ("Regime hibrido com dois dias presenciais.", 2),
        ("Vaga presencial, 5 dias por semana.", 5),
    ],
)
def test_the_number_of_office_days_is_read(texto, dias):
    assert ler(texto).dias == dias


def test_hybrid_without_a_number_says_hybrid_and_not_a_number():
    """Nao saber quantos dias nao e o mesmo que saber que sao zero."""
    achado = ler("Modelo de trabalho hibrido, combinado com o time.")
    assert achado.hibrido is True
    assert achado.dias is None


def test_a_count_that_is_not_the_week_is_ignored():
    """"10 dias" nao cabe numa semana: casou outra contagem."""
    assert ler("Ferias de 10 dias por semestre presencial.").dias is None


def test_a_description_without_any_of_this_says_nothing():
    achado = ler("Buscamos pessoa para cuidar de Kubernetes.")
    assert achado.hibrido is False
    assert achado.dias is None


def test_an_empty_description_is_not_an_error():
    assert ler(None).dias is None
    assert ler("").hibrido is False
