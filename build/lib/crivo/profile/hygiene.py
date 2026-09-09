"""Diagnostico de problemas do proprio historico do candidato.

O que este modulo produz nao e sobre vagas: e sobre o perfil de quem procura. A
lista existe sempre, vazia inclusive, porque ausencia de diagnostico e um
resultado e nao um silencio -- uma lista omitida deixaria a interface sem saber
se nada foi encontrado ou se nada foi verificado.

Cada problema carrega o campo de origem e o trecho de dado que o motivou. Sem a
evidencia, o aviso vira opiniao e o usuario nao tem como conferir.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import periodo

#: Meses de intervalo a partir dos quais uma lacuna vira observacao.
LACUNA_MINIMA_MESES = 6

CARGOS_ATUAIS_SIMULTANEOS = "cargos_atuais_simultaneos"
LACUNA_TEMPORAL = "lacuna_temporal"
SOBREPOSICAO = "sobreposicao_de_periodo"
PERIODO_ILEGIVEL = "periodo_ilegivel"


@dataclass(frozen=True)
class HygieneIssue:
    """Um problema detectado, com a evidencia que o sustenta."""

    tipo: str
    campo: str
    trecho: str
    detalhe: str


def _meses(valor) -> int | None:
    """Converte o periodo em meses absolutos. `None` quando ilegivel.

    A leitura mora em `periodo` porque a escolha da experiencia mais recente
    precisa exatamente da mesma. Duas interpretacoes de data no mesmo perfil
    fariam a higiene reclamar de um periodo que a inferencia de nivel leu sem
    problema, ou o contrario.
    """
    return periodo.meses_absolutos(valor)


def _rotulo(experiencia: dict) -> str:
    titulo = experiencia.get("titulo") or "cargo sem titulo"
    empresa = experiencia.get("empresa") or "empresa nao informada"
    return f"{titulo} — {empresa}"


def diagnose(experiencias: list | None) -> list[HygieneIssue]:
    """Devolve os problemas encontrados. Lista vazia quando nao ha nenhum."""
    validas = [e for e in (experiencias or []) if isinstance(e, dict)]
    problemas: list[HygieneIssue] = []

    atuais = [e for e in validas if not e.get("fim")]
    if len(atuais) > 1:
        problemas.append(
            HygieneIssue(
                tipo=CARGOS_ATUAIS_SIMULTANEOS,
                campo="experiencias",
                trecho="; ".join(_rotulo(e) for e in atuais),
                detalhe=(
                    f"{len(atuais)} cargos aparecem como atuais ao mesmo tempo. "
                    "Recrutador le isso como perfil desatualizado ou desatento"
                ),
            )
        )

    periodos = []
    for experiencia in validas:
        inicio = _meses(experiencia.get("inicio"))
        if inicio is None:
            problemas.append(
                HygieneIssue(
                    tipo=PERIODO_ILEGIVEL,
                    campo="experiencias.inicio",
                    trecho=_rotulo(experiencia),
                    detalhe=(
                        f"inicio {experiencia.get('inicio')!r} nao pode ser lido; "
                        "use o formato AAAA-MM"
                    ),
                )
            )
            continue
        fim = _meses(experiencia.get("fim"))
        periodos.append((inicio, fim, experiencia))

    periodos.sort(key=lambda item: item[0])

    for anterior, seguinte in zip(periodos, periodos[1:]):
        inicio_a, fim_a, exp_a = anterior
        inicio_b, _fim_b, exp_b = seguinte
        if fim_a is None:
            continue
        if inicio_b > fim_a + LACUNA_MINIMA_MESES:
            problemas.append(
                HygieneIssue(
                    tipo=LACUNA_TEMPORAL,
                    campo="experiencias",
                    trecho=f"{_rotulo(exp_a)} -> {_rotulo(exp_b)}",
                    detalhe=(
                        f"{inicio_b - fim_a} meses sem cargo declarado entre os dois. "
                        "Lacuna sem explicacao costuma virar pergunta na entrevista"
                    ),
                )
            )
        if inicio_b < fim_a:
            problemas.append(
                HygieneIssue(
                    tipo=SOBREPOSICAO,
                    campo="experiencias",
                    trecho=f"{_rotulo(exp_a)} -> {_rotulo(exp_b)}",
                    detalhe=(
                        f"{fim_a - inicio_b} meses de sobreposicao entre os periodos. "
                        "Se foi promocao interna, vale unir os cargos"
                    ),
                )
            )
    return problemas


def as_dicts(problemas: list[HygieneIssue]) -> list[dict]:
    """Forma serializavel, para gravacao junto da versao do perfil."""
    return [
        {
            "tipo": p.tipo,
            "campo": p.campo,
            "trecho": p.trecho,
            "detalhe": p.detalhe,
        }
        for p in problemas
    ]
