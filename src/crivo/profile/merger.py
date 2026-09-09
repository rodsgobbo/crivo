"""Consolidacao do perfil-alvo a partir das origens conectadas.

Cada mudanca de origem produz uma versao nova e imutavel, em vez de alterar a
anterior. Isso torna possivel explicar um relatorio antigo pelo perfil que o
produziu, e medir se mexer no perfil adiantou -- que e o unico indicador real de
eficacia do produto.

A precedencia entre origens vem da configuracao e reflete a realidade da
plataforma: os escopos abertos do LinkedIn devolvem identidade e nao devolvem
historico, entao o curriculo carrega o conteudo e a conexao carrega a
identidade. Inverter a ordem faria a identidade sobrescrever o conteudo.

Cada campo do resultado guarda a origem que forneceu o valor vigente, porque um
usuario que ve algo errado precisa saber de onde aquilo veio para corrigir no
lugar certo.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..store.repository import Repository
from . import hygiene
from .seniority import infer_from_experiences

#: Campos do perfil-alvo. Lista fechada, como a da extracao.
CAMPOS = (
    "nome",
    "headline",
    "localizacao",
    "experiencias",
    "competencias",
    "formacao",
    "idiomas",
)

#: Campos sem os quais nao ha o que buscar.
CAMPOS_DE_HISTORICO = ("experiencias",)

MANUAL = "manual"
RESUME = "resume"
LINKEDIN = "linkedin"


class ProfileError(Exception):
    """O perfil-alvo nao pode ser consolidado ou nao serve para um run."""


class HistoryRequired(ProfileError):
    """Nenhuma origem forneceu historico profissional."""


@dataclass(frozen=True)
class ProfileVersion:
    """Uma versao consolidada do perfil-alvo."""

    version_id: str
    campos: dict[str, Any]
    origem_por_campo: dict[str, str]
    nivel_inferido: str
    problemas_higiene: list[dict]
    criado_em: str

    @property
    def tem_historico(self) -> bool:
        return any(self.campos.get(c) for c in CAMPOS_DE_HISTORICO)


class ProfileMerger:
    """Consolida, versiona e diagnostica o perfil-alvo de cada usuario."""

    def __init__(self, connection, config) -> None:
        self._repository = Repository(connection)
        self._precedencia = tuple(config.profile.precedencia_origens)

    # ---------------------------------------------------------- consolidacao
    def consolidate(
        self,
        user_id: str,
        resume_fields: dict | None = None,
        manual_fields: dict | None = None,
        linkedin_fields: dict | None = None,
    ) -> ProfileVersion:
        """Cria uma versao nova a partir das origens informadas."""
        por_origem = {
            MANUAL: manual_fields or {},
            RESUME: resume_fields or {},
            LINKEDIN: linkedin_fields or {},
        }
        campos: dict[str, Any] = {}
        origem_por_campo: dict[str, str] = {}
        for campo in CAMPOS:
            for origem in self._precedencia:
                valor = por_origem.get(origem, {}).get(campo)
                if valor:
                    campos[campo] = valor
                    origem_por_campo[campo] = origem
                    break
            else:
                campos[campo] = None

        senioridade = infer_from_experiences(campos.get("experiencias"))
        problemas = hygiene.as_dicts(hygiene.diagnose(campos.get("experiencias")))
        return self._store(user_id, campos, origem_por_campo, senioridade.nivel, problemas)

    def current(self, user_id: str) -> ProfileVersion | None:
        """Versao vigente, que e a mais recente."""
        # Ordena por rowid e nao por `criado_em`: o carimbo e truncado em
        # segundos, e duas versoes criadas dentro do mesmo segundo ficariam em
        # ordem indefinida. O rowid e monotonico na insercao, entao a versao
        # vigente e sempre a ultima gravada.
        rows = self._repository.for_user(user_id).select(
            "profile_versions", order_by="rowid DESC", limit=1
        )
        return _to_version(rows[0]) if rows else None

    def current_or_consolidate(
        self, user_id: str, ultima_alteracao: str | None, **origens
    ) -> ProfileVersion:
        """Reusa a versao vigente enquanto nenhuma origem mudou depois dela."""
        vigente = self.current(user_id)
        if (
            vigente is not None
            and ultima_alteracao is not None
            and vigente.criado_em >= ultima_alteracao
        ):
            return vigente
        return self.consolidate(user_id, **origens)

    def history(self, user_id: str) -> list[ProfileVersion]:
        return [
            _to_version(row)
            for row in self._repository.for_user(user_id).select(
                "profile_versions", order_by="rowid"
            )
        ]

    # ------------------------------------------------------------ portao
    def require_for_run(self, user_id: str) -> ProfileVersion:
        """Devolve o perfil que serve a um run, ou recusa nomeando o que falta."""
        vigente = self.current(user_id)
        if vigente is None:
            raise HistoryRequired(
                "nenhum perfil consolidado; importe um curriculo antes de buscar vagas"
            )
        if not vigente.tem_historico:
            raise HistoryRequired(
                "nenhuma origem forneceu historico profissional; importe um "
                "curriculo com suas experiencias antes de buscar vagas"
            )
        return vigente

    # ------------------------------------------------------------ interno
    def _store(
        self,
        user_id: str,
        campos: dict,
        origem_por_campo: dict,
        nivel: str,
        problemas: list[dict],
    ) -> ProfileVersion:
        version_id = str(uuid.uuid4())
        criado_em = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._repository.for_user(user_id).insert(
            "profile_versions",
            {
                "version_id": version_id,
                "campos": json.dumps(campos, ensure_ascii=False),
                "origem_por_campo": json.dumps(origem_por_campo, ensure_ascii=False),
                "nivel_inferido": nivel,
                "problemas_higiene": json.dumps(problemas, ensure_ascii=False),
                "criado_em": criado_em,
            },
        )
        return ProfileVersion(
            version_id=version_id,
            campos=campos,
            origem_por_campo=origem_por_campo,
            nivel_inferido=nivel,
            problemas_higiene=problemas,
            criado_em=criado_em,
        )


def _to_version(row) -> ProfileVersion:
    return ProfileVersion(
        version_id=row["version_id"],
        campos=json.loads(row["campos"]),
        origem_por_campo=json.loads(row["origem_por_campo"]),
        nivel_inferido=row["nivel_inferido"],
        problemas_higiene=json.loads(row["problemas_higiene"]),
        criado_em=row["criado_em"],
    )
