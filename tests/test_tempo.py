"""Apresentacao de carimbo de tempo.

O banco grava UTC de proposito -- contador diario e ordenacao de fila dependem
de um relogio unico. Mostrar UTC na tela, porem, e outra coisa: um run pedido as
09:21 aparecendo como "12:21" faz a pagina afirmar algo que o usuario sabe ser
falso, e isso derruba a confianca no resto do que ela mostra.
"""

from datetime import datetime, timezone

from crivo.web.tempo import local, local_completo


def _esperado(iso: str, formato: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime(formato)


def test_an_utc_stamp_is_shown_in_the_local_timezone():
    iso = "2026-08-24T12:21:28+00:00"
    assert local(iso) == _esperado(iso, "%d/%m %H:%M")


def test_a_stamp_without_timezone_is_assumed_to_be_utc():
    """Carimbo sem fuso vem do banco, onde tudo e UTC por convencao."""
    assert local("2026-08-24T12:21:28") == local("2026-08-24T12:21:28+00:00")


def test_the_long_form_carries_the_year():
    iso = "2026-08-24T12:21:28+00:00"
    assert local_completo(iso) == _esperado(iso, "%d/%m/%Y %H:%M")


def test_an_unreadable_stamp_is_shown_as_it_came():
    """Melhor exibir cru do que esconder atras de um traco."""
    assert local("ontem de manha") == "ontem de manha"


def test_an_absent_stamp_becomes_empty_text():
    assert local(None) == ""
    assert local("") == ""
