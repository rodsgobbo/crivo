"""Enriquecimento das vagas que sobreviveram ao pre-filtro.

Este modulo roda no processo unico de enriquecimento. Ser unico na instalacao e
o que faz a serializacao exigida pela contencao ser consequencia da topologia, e
nao uma trava que alguem precisa lembrar de adquirir antes de cada chamada.

O desenho original dividia o trabalho entre duas rotas: a publica trazia a
descricao e uma sessao de conta operacional traria o que se supunha exclusivo
dela -- sinais do card, concorrencia e vagas recomendadas. Essa sessao nunca foi
construida, e medido contra a origem real o pressuposto se mostrou falso: a
propria rota publica devolve os sinais e a contagem de candidatos junto com a
descricao, numa requisicao so. Restava apenas as vagas recomendadas, que saiiram
de escopo com a camada operacional.

O enriquecedor e o unico componente que grava o registro compartilhado de
descricoes. Concentrar a gravacao aqui mantem o acerto de cache e o debito de
cota numa decisao so; deixar o coletor gravar descricao furaria os dois.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..store.repository import Repository
from .governor import CollectionBlocked, RateGovernor
from .sources.guest import CollectionError, JobSource, data_de_publicacao

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentOutcome:
    """O que o enriquecimento produziu para um run."""

    enriquecidas: list[str] = field(default_factory=list)
    reusadas: list[str] = field(default_factory=list)
    sem_descricao: list[str] = field(default_factory=list)
    sem_cota: list[str] = field(default_factory=list)
    bloqueado: bool = False

    @property
    def pagas(self) -> int:
        return len(self.enriquecidas)


class Enricher:
    """Busca descricao, sinais e concorrencia das vagas sobreviventes."""

    def __init__(
        self,
        connection,
        source: JobSource,
        governor: RateGovernor,
        quota=None,
    ) -> None:
        self._repository = Repository(connection)
        self._source = source
        self._governor = governor
        self._quota = quota

    # ---------------------------------------------------- registro partilhado
    def stored_description(self, job_id: str) -> dict | None:
        """Descricao ja coletada por qualquer usuario, se existir."""
        linhas = self._repository.select(
            "job_descriptions", where="job_id = ?", params=(job_id,)
        )
        if not linhas:
            return None
        linha = linhas[0]
        return {
            "texto": linha["texto"],
            "emails": json.loads(linha["emails_contato"] or "[]"),
            "candidatos": linha["candidatos"],
            "coletada_em": linha["coletada_em"],
        }

    def _store_description(
        self, job_id: str, texto: str, emails: list[str],
        candidatos: int | None = None, publicada_ha: str | None = None,
    ) -> None:
        # Descricao coletada nunca expira: o texto de um anuncio nao muda e cada
        # nova leitura seria risco sem retorno.
        self._repository.insert(
            "job_descriptions",
            {
                "job_id": job_id,
                "texto": texto,
                "emails_contato": json.dumps(sorted(set(emails)), ensure_ascii=False),
                "candidatos": candidatos,
                "publicada_ha": publicada_ha,
                "coletada_em": _stamp(),
            },
        )
        self._backfill_publication_date(job_id, publicada_ha)

    def _backfill_publication_date(self, job_id: str, publicada_ha: str | None) -> None:
        """Preenche a data da vaga quando a busca nao a trouxe.

        O card chega sem data numa janela estreita -- a origem marca anuncio
        recente com outra classe de marcacao e a biblioteca so procura a antiga.
        A pagina do anuncio, que ja foi buscada aqui, diz a recencia por
        extenso; converte-la fecha o buraco sem nenhuma requisicao a mais.

        So preenche o que esta vazio. Data vinda da busca e afirmacao da origem
        e vence a derivada de "ha 3 semanas", que tem a granularidade da frase.
        """
        data = data_de_publicacao(publicada_ha)
        if data is None:
            return
        self._repository.execute(
            "UPDATE jobs SET publicada_em = ? "
            "WHERE job_id = ? AND publicada_em IS NULL",
            (data, job_id),
        )

    def _store_signals(self, user_id: str, job_id: str, sinais: list[str]) -> None:
        """Sinais que vieram com a descricao, na vaga do usuario."""
        if not sinais:
            return
        self._repository.for_user(user_id).update(
            "jobs",
            {"flags": json.dumps(sorted(set(sinais)), ensure_ascii=False)},
            where="job_id = ?",
            params=(job_id,),
        )

    # ------------------------------------------------------------ execucao
    def enrich(self, user_id: str, cards: list) -> EnrichmentOutcome:
        """Enriquece os sobreviventes, na ordem em que foram entregues."""
        resultado = EnrichmentOutcome()

        for card in cards:
            if resultado.bloqueado:
                resultado.sem_descricao.append(card.job_id)
                continue

            guardada = self.stored_description(card.job_id)
            if guardada is not None:
                # Acerto no registro partilhado: nao houve requisicao, entao nao
                # ha cota a debitar. E isso que torna o segundo usuario de uma
                # vaga popular gratuito.
                resultado.reusadas.append(card.job_id)
                continue

            if self._quota is not None and not self._quota.can_spend(user_id):
                resultado.sem_cota.append(card.job_id)
                continue

            try:
                dados = self._governor.run(
                    lambda c=card: self._source.describe(c.job_id, c.url)
                )
            except CollectionBlocked:
                resultado.bloqueado = True
                resultado.sem_descricao.append(card.job_id)
                logger.warning("enriquecimento interrompido por bloqueio de coleta")
                continue
            except CollectionError as exc:
                resultado.sem_descricao.append(card.job_id)
                logger.info("vaga %s ficou sem descricao: %s", card.job_id, exc)
                continue

            texto = (dados.get("texto") or "").strip()
            if not texto:
                resultado.sem_descricao.append(card.job_id)
                continue

            self._store_description(
                card.job_id, texto, dados.get("emails") or [],
                candidatos=dados.get("candidatos"),
                publicada_ha=dados.get("publicada_ha"),
            )
            self._store_signals(user_id, card.job_id, dados.get("sinais") or [])
            if self._quota is not None:
                self._quota.spend(user_id)
            resultado.enriquecidas.append(card.job_id)

        return resultado

    def has_description(self, job_id: str) -> bool:
        return self.stored_description(job_id) is not None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
