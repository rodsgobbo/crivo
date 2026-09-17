"""Lacunas, diferenciais e agregacao de competencias por frequencia.

Esta e a parte do produto que produz recomendacao acionavel sem custo nenhum de
modelo. "A vaga cita PostgreSQL, DynamoDB e Datadog, ausentes no seu perfil" e
uma frase que o usuario executa hoje, e ela sai de uma diferenca entre conjuntos.

As listas reusam exatamente os conjuntos canonicos que compuseram o componente
de competencias do score. Recalcular por outro caminho abriria espaco para o
relatorio dizer que falta uma competencia que o score contou como presente.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .ontology import Ontology


@dataclass(frozen=True)
class SkillGap:
    """O que a vaga pede e o perfil nao tem, e o contrario."""

    lacunas: tuple[str, ...]
    diferenciais: tuple[str, ...]

    @property
    def tem_lacuna(self) -> bool:
        return bool(self.lacunas)


def compare(
    ontology: Ontology, competencias_perfil, competencias_vaga
) -> SkillGap:
    """Diferenca entre o que a vaga pede e o que o perfil declara."""
    perfil = ontology.canonical_set(competencias_perfil)
    vaga = (
        competencias_vaga
        if isinstance(competencias_vaga, frozenset)
        else ontology.canonical_set(competencias_vaga)
    )
    return SkillGap(
        lacunas=tuple(sorted(vaga - perfil)),
        diferenciais=tuple(sorted(perfil & vaga)),
    )


def from_description(
    ontology: Ontology, competencias_perfil, descricao: str | None
) -> SkillGap:
    """Compara o perfil com as competencias reconhecidas numa descricao."""
    return compare(ontology, competencias_perfil, ontology.extract(descricao))


def aggregate(ontology: Ontology, descricoes) -> list[tuple[str, int]]:
    """Frequencia de cada competencia entre as vagas com descricao do run.

    Vagas sem descricao ficam de fora em vez de contarem como zero: incluir uma
    vaga que nao foi lida faria o ranking parecer mais raro do que e.
    """
    contagem: Counter[str] = Counter()
    for descricao in descricoes or []:
        if not (descricao or "").strip():
            continue
        contagem.update(ontology.extract(descricao))
    # Empate resolvido por nome, para que o ranking seja estavel entre runs.
    return sorted(contagem.items(), key=lambda item: (-item[1], item[0]))


def missing_across(
    ontology: Ontology, competencias_perfil, descricoes
) -> list[tuple[str, int]]:
    """Competencias mais pedidas que o perfil nao declara."""
    perfil = ontology.canonical_set(competencias_perfil)
    return [
        (termo, quantas)
        for termo, quantas in aggregate(ontology, descricoes)
        if termo not in perfil
    ]


def missing_for_profile(
    ontology: Ontology, campos: dict, descricoes
) -> list[tuple[str, int]]:
    """Mais pedidas que o candidato nao tem, nem declaradas nem pelo historico.

    E o mesmo conjunto que as lacunas de cada vaga usam. Comparar o ranking so
    com a lista declarada faria a mesma pagina dizer duas coisas opostas: o card
    da vaga sem lacuna de SRE, e o ranking mandando acrescentar SRE ao perfil.
    """
    from .ontology import skills_from_profile

    return missing_across(
        ontology, skills_from_profile(ontology, campos), descricoes
    )
