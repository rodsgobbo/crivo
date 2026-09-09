"""Inferencia deterministica de nivel a partir do cargo mais recente.

O conjunto de regras e ordenado e o primeiro casamento vence. A ordem importa e
nao e alfabetica: um titulo como "Head de Engenharia de Plataforma" casaria com
mais de um padrao, e a ordem decide qual deles descreve o cargo. Do mais alto
para o mais baixo evita que "Engenheiro" numa frase como "Head de Engenharia"
rebaixe um cargo executivo.

Nada aqui chama modelo de linguagem. Dois runs do mesmo perfil precisam produzir
o mesmo nivel, porque o nivel decide as buscas e uma variacao silenciosa faria a
diferenca de resultado entre dois dias parecer movimento de mercado.
"""

from __future__ import annotations

import re

from . import periodo
from dataclasses import dataclass

EXECUTIVE = "executive"
MANAGER = "manager"
LEAD = "lead"
SENIOR = "senior"
PLENO = "pleno"

#: Regras em ordem de precedencia. O primeiro casamento vence.
REGRAS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(cto|cio|ctso|vp|vice[- ]president|chief|c-level)\b"), EXECUTIVE),
    (re.compile(r"(?i)\b(diretor[ae]?|director|head)\b"), EXECUTIVE),
    (
        re.compile(
            r"(?i)\b(gerente|ger[êe]ncia|manager|coordenador[ae]?|coordinator|"
            r"supervisor[ae]?)\b"
        ),
        MANAGER,
    ),
    (
        re.compile(r"(?i)\b(staff|principal|especialista|specialist|lead|l[íi]der)\b"),
        LEAD,
    ),
    (re.compile(r"(?i)\b(s[êe]nior|senior|sr\.?)\b"), SENIOR),
)

#: Sinais de gestao no texto da experiencia. Elevam a nivel de gestao um titulo
#: que sozinho nao revelaria isso, como "Especialista" de quem lidera um time.
SINAIS_DE_GESTAO = re.compile(
    r"(?i)(lidero|liderei|lideran[çc]a de time|gest[ãa]o de (time|equipe|pessoas)|"
    r"reportes diretos|liderou (um )?time|gerenciei|people manage)"
)

#: Ordem de senioridade, do menor para o maior.
ESCALA = (PLENO, SENIOR, LEAD, MANAGER, EXECUTIVE)


@dataclass(frozen=True)
class Seniority:
    """Nivel inferido, com a evidencia que o produziu."""

    nivel: str
    evidencia: str
    origem: str


def infer(titulo: str | None, descricao: str | None = None) -> Seniority:
    """Devolve o nivel do cargo, ou `pleno` quando nada casa."""
    texto = titulo or ""
    for padrao, nivel in REGRAS:
        encontrado = padrao.search(texto)
        if encontrado:
            resultado = Seniority(
                nivel=nivel, evidencia=encontrado.group(0), origem="titulo"
            )
            return _reforcar(resultado, descricao)
    return _reforcar(
        Seniority(nivel=PLENO, evidencia="", origem="padrao"), descricao
    )


def _reforcar(atual: Seniority, descricao: str | None) -> Seniority:
    """Eleva a nivel de gestao quando a descricao declara liderar time."""
    if ESCALA.index(atual.nivel) >= ESCALA.index(MANAGER):
        return atual
    encontrado = SINAIS_DE_GESTAO.search(descricao or "")
    if not encontrado:
        return atual
    return Seniority(
        nivel=MANAGER, evidencia=encontrado.group(0), origem="descricao"
    )


def most_recent(experiencias: list | None) -> dict | None:
    """Escolhe a experiencia mais recente: cargo atual vence, senao o maior inicio.

    O desempate le a data como data. Comparar `inicio` como texto ordenava pelo
    alfabeto -- "Out/2022" depois de "Ago/2025" -- e num curriculo real isso
    elegeu uma associacao voluntaria, ainda em curso, como cargo atual, e o
    nivel do candidato despencou de gestor para pleno.
    """
    if not experiencias:
        return None
    validas = [e for e in experiencias if isinstance(e, dict)]
    if not validas:
        return None
    atuais = [e for e in validas if _sem_fim(e)]
    candidatas = atuais or validas
    # Ilegivel vai para o fim da fila em vez de vencer por acaso: `-1` perde de
    # qualquer data valida, e entre dois ilegiveis a ordem original decide.
    return max(candidatas, key=lambda e: periodo.meses_absolutos(e.get("inicio")) or -1)


def _sem_fim(experiencia: dict) -> bool:
    """O cargo esta em curso? `fim` vazio ou dizendo "atual" contam igual."""
    fim = experiencia.get("fim")
    return not fim or periodo.em_curso(fim)


def infer_from_experiences(experiencias: list | None) -> Seniority:
    """Aplica a inferencia sobre a experiencia mais recente."""
    recente = most_recent(experiencias)
    if recente is None:
        return Seniority(nivel=PLENO, evidencia="", origem="padrao")
    return infer(recente.get("titulo"), recente.get("descricao"))
