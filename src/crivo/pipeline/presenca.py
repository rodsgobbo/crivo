"""Quantos dias de escritorio a vaga exige, lidos da descricao.

A origem responde uma coisa so sobre isso: remoto, sim ou nao. Nao existe
"hibrido" no dado que a coleta traz, e por isso uma vaga que pede tres dias
por semana no escritorio chega marcada como remota -- e uma vaga remota nunca e
avaliada geograficamente. O deslocamento que ela exige some do sistema inteiro.

Quem sabe disso e a descricao, que so existe depois do enriquecimento. Ler o
numero aqui e o unico jeito de a passada final recuperar a distancia que o
pre-filtro nao tinha como calcular.

A leitura e por padrao de texto, como o resto do que se le de anuncio: classe
de CSS muda a cada versao da pagina, "3 dias por semana no escritorio" nao.
Reconhecer formato e codigo, e nao politica -- e o mesmo lugar onde
`profile/periodo.py` le data de experiencia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .prefilter import normalize

#: Prefixo do aviso, ao lado de `geografia:` e `trilha:` no mesmo campo.
PREFIXO_PRESENCA = "presenca:"

#: Numeros por extenso que aparecem em anuncio, nos dois idiomas que a coleta
#: traz. Acima de cinco nao faz sentido: a semana acaba.
_POR_EXTENSO = {
    "um": 1, "uma": 1, "one": 1,
    "dois": 2, "duas": 2, "two": 2,
    "tres": 3, "three": 3,
    "quatro": 4, "four": 4,
    "cinco": 5, "five": 5,
}

#: Marcas de que a vaga mistura escritorio e casa, sem dizer quanto de cada.
_MARCAS = ("hibrido", "hybrid", "semipresencial", "semi presencial")

#: "3 dias por semana no escritorio", "2x na semana presencial", "3 days in the
#: office". O numero vem primeiro e a palavra que o qualifica vem logo depois,
#: dentro da mesma frase -- o limite de caracteres existe para o padrao nao
#: casar um numero de uma frase com o "escritorio" da seguinte.
_DIAS = re.compile(
    r"(?P<n>\d+|" + "|".join(_POR_EXTENSO) + r")\s*"
    r"(?:x|dias?|days?)\b"
    r"[^.;\n]{0,40}?"
    r"(?:semana|week|escritorio|office|presencial|presenciais|presencialmente)"
)


@dataclass(frozen=True)
class Presenca:
    """O que a descricao diz sobre ir ao escritorio."""

    hibrido: bool = False
    #: `None` quando o texto nao diz quantos dias, inclusive quando diz
    #: "hibrido" e para por ai. Nao saber quantos dias nao e o mesmo que zero.
    dias: int | None = None


def ler(descricao: str | None) -> Presenca:
    """Le a exigencia de presenca de uma descricao de vaga."""
    alvo = normalize(descricao)
    if not alvo:
        return Presenca()

    hibrido = any(marca in alvo for marca in _MARCAS)
    achado = _DIAS.search(alvo)
    if achado is None:
        return Presenca(hibrido=hibrido)

    bruto = achado.group("n")
    dias = _POR_EXTENSO.get(bruto) or (int(bruto) if bruto.isdigit() else None)
    if dias is None or not 0 <= dias <= 5:
        # Fora da semana: quase sempre outra contagem que casou por acaso,
        # como "10 dias de ferias". Marcar o numero errado e pior que nenhum.
        return Presenca(hibrido=hibrido)
    return Presenca(hibrido=True, dias=dias)
