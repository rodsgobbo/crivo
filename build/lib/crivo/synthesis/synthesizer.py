"""Estagio terminal de sintese: uma requisicao logica por run.

Falha de rede, resposta invalida, cadeia de provedores esgotada e violacao de
aterramento convergem para o mesmo efeito operacional: falha de sintese gravada
no run e relatorio gerado sem essa secao. Tratar os quatro casos de forma
diferente daria ao usuario quatro maneiras de nao receber a mesma coisa.

O que o run guarda nao e so o texto: guarda o provedor, o modelo e a contagem de
tokens. Sem isso, uma sintese ruim seria um misterio, e o custo do dia seria uma
estimativa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..providers.client import DeterministicFallback, ModelClient, ModelError
from ..worker.queue import RunQueue
from . import grounding, prompt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthesisResult:
    """O que a sintese produziu, ou por que nao produziu."""

    texto: str | None
    provedor: str | None = None
    modelo: str | None = None
    tokens_entrada: int = 0
    tokens_saida: int = 0
    falha: str | None = None

    @property
    def disponivel(self) -> bool:
        return self.texto is not None


class Synthesizer:
    """Monta, envia, verifica e registra a sintese de um run."""

    def __init__(self, connection, client: ModelClient | None, config) -> None:
        self._queue = RunQueue(connection)
        self._client = client
        self._limite_vagas = config.synthesis.limite_vagas_enviadas
        self._limite_descricao = config.synthesis.limite_caracteres_texto_externo
        self._deterministico = config.synthesis.modo_deterministico

    def synthesize(
        self,
        user_id: str,
        run_id: str,
        perfil: dict,
        vagas: list[dict],
        descartadas: list[dict],
        contexto: dict,
    ) -> SynthesisResult:
        """Executa a sintese e grava o resultado no registro do run."""
        if self._deterministico or self._client is None:
            return self._registrar(
                run_id, SynthesisResult(texto=None, falha="modo deterministico")
            )

        payload = prompt.build(
            perfil=perfil,
            vagas=vagas,
            descartadas=descartadas,
            contexto=contexto,
            limite_vagas=self._limite_vagas,
            limite_descricao=self._limite_descricao,
        )
        if payload.vagas == 0:
            return self._registrar(
                run_id, SynthesisResult(texto=None, falha="nenhuma vaga a sintetizar")
            )

        self._client.assert_owner(user_id)
        try:
            resposta = self._client.complete("sintese_vagas", payload.corpo)
        except DeterministicFallback as exc:
            return self._registrar(
                run_id, SynthesisResult(texto=None, falha=f"cadeia esgotada: {exc}")
            )
        except ModelError as exc:
            return self._registrar(
                run_id, SynthesisResult(texto=None, falha=f"falha de modelo: {exc}")
            )

        ha_sem_descricao = any(not v.get("descricao") for v in vagas[: payload.vagas])
        try:
            texto = grounding.accept(resposta.texto, payload, ha_sem_descricao)
        except grounding.GroundingViolation as exc:
            logger.warning("violacao de aterramento no run %s: %s", run_id, exc)
            return self._registrar(
                run_id,
                SynthesisResult(
                    texto=None,
                    provedor=resposta.provedor,
                    modelo=resposta.modelo,
                    tokens_entrada=resposta.tokens_entrada,
                    tokens_saida=resposta.tokens_saida,
                    falha=f"violacao de aterramento: {exc}",
                ),
            )

        return self._registrar(
            run_id,
            SynthesisResult(
                texto=texto,
                provedor=resposta.provedor,
                modelo=resposta.modelo,
                tokens_entrada=resposta.tokens_entrada,
                tokens_saida=resposta.tokens_saida,
            ),
        )

    # ------------------------------------------------------------ interno
    def _registrar(self, run_id: str, resultado: SynthesisResult) -> SynthesisResult:
        self._queue.record_synthesis(
            run_id,
            provedor=resultado.provedor,
            modelo=resultado.modelo,
            tokens_entrada=resultado.tokens_entrada,
            tokens_saida=resultado.tokens_saida,
            falha=resultado.falha,
            texto=resultado.texto,
        )
        return resultado
