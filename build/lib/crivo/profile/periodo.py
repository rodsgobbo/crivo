"""Leitura de periodo de experiencia, num lugar so.

Curriculo nao vem em ISO. Ele vem em "Ago/2025", "ago 2025", "08/2025" e
"2025-08", porque quem escreve e uma pessoa e nao um sistema -- e o extrator
devolve o que estava escrito, deliberadamente, para que a conferencia mostre o
mesmo que o documento diz.

Antes deste modulo cada consumidor interpretava data do seu jeito, e o que nao
era ISO virava `None`: a higiene reportava "periodo ilegivel" e a escolha da
experiencia mais recente caia numa comparacao de texto, onde "Out/2022" vem
depois de "Ago/2025" porque O vem depois de A. Num caso real isso elegeu uma
associacao voluntaria como cargo atual e rebaixou um Engineering Manager a
pleno, o que por sua vez enviesou toda a busca para vagas de nivel abaixo.

O que nao se le continua sendo `None`. A tolerancia e sobre o formato, e nunca
sobre inventar uma data que o documento nao afirma.
"""

from __future__ import annotations

import re

#: Meses por prefixo de tres letras, sem acento. Cobre portugues e ingles, que
#: e o que aparece num curriculo brasileiro de tecnologia.
MESES: dict[str, int] = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8,
    "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11,
    "dez": 12, "dec": 12,
}

#: Formas que indicam cargo em curso. Um periodo em aberto nao e ilegivel.
EM_CURSO = frozenset({
    "atual", "atualmente", "presente", "hoje", "current", "present", "now",
})

_ACENTOS = str.maketrans("áàâãéêíóôõúüç", "aaaaeeiooouuc")


def normalizar(valor) -> str:
    return str(valor or "").strip().lower().translate(_ACENTOS)


def em_curso(valor) -> bool:
    """O periodo declara que o cargo continua?"""
    return normalizar(valor) in EM_CURSO


def meses_absolutos(valor) -> int | None:
    """Converte um periodo em meses desde o ano zero. `None` se ilegivel.

    Aceita, nesta ordem: ano isolado, `AAAA-MM` e `AAAA/MM`, `MM-AAAA` e
    `MM/AAAA`, e mes por extenso abreviado como `Ago/2025` ou `ago 2025`.
    """
    texto = normalizar(valor)
    if not texto or texto in EM_CURSO:
        return None

    # Mes por extenso: a forma mais comum em curriculo brasileiro.
    achado = re.search(r"([a-z]{3,})[\s/.-]+(\d{4})", texto)
    if achado:
        mes = MESES.get(achado.group(1)[:3])
        if mes:
            return _absolutos(int(achado.group(2)), mes)

    # Daqui para baixo so entram formas puramente numericas. Aceitar um numero
    # solto no meio de prosa faria "outono de 2015" virar janeiro de 2015 -- e
    # o documento nao afirma mes nenhum ali. Ilegivel precisa continuar
    # ilegivel, porque e assim que a higiene pede a correcao ao usuario.
    if not re.fullmatch(r"[\d\s/.-]+", texto):
        return None

    numeros = [int(n) for n in re.findall(r"\d+", texto)]
    if not numeros:
        return None
    if len(numeros) == 1:
        return _absolutos(numeros[0], 1) if numeros[0] > 31 else None

    primeiro, segundo = numeros[0], numeros[1]
    # Quem tem quatro digitos e o ano, independente da posicao. Isso resolve
    # `2025-08` e `08/2025` sem precisar adivinhar a convencao do autor.
    if primeiro > 31:
        return _absolutos(primeiro, segundo)
    if segundo > 31:
        return _absolutos(segundo, primeiro)
    return None


def _absolutos(ano: int, mes: int) -> int | None:
    if not (1900 <= ano <= 2200) or not (1 <= mes <= 12):
        return None
    return ano * 12 + (mes - 1)
