"""Extracao estruturada do curriculo, com conferencia humana como portao.

A extracao usa o provedor do proprio usuario, e a qualidade disso varia muito
entre um modelo grande e um modelo pequeno de cota gratuita. O desenho responde
tornando a conferencia um estado no banco e nao um passo de interface: um
registro nasce com `confirmado_em` nulo, e o consolidador de perfil ignora
registro nao confirmado. Se o modelo do usuario errar, o erro para na tela de
conferencia em vez de contaminar buscas, score e recomendacoes em silencio.

A proveniencia de cada extracao fica gravada. Sem ela, um perfil ruim seria um
misterio; com ela, e possivel dizer qual provedor e qual modelo o produziram.

Correcao do usuario e gravada ao lado do valor extraido, nunca por cima: manter
os dois e o que permite comparar o que o modelo leu com o que o humano corrigiu.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..providers.client import DeterministicFallback, ModelClient, ModelError
from ..store.repository import Repository

#: Campos que a extracao tenta preencher. A lista e fechada: campo fora dela e
#: descartado, para que uma resposta criativa do modelo nao invente estrutura.
CAMPOS = (
    "nome",
    "headline",
    "localizacao",
    "experiencias",
    "competencias",
    "formacao",
    "idiomas",
)

INSTRUCAO = (
    "Extraia do curriculo abaixo um objeto JSON com exatamente estas chaves: "
    + ", ".join(CAMPOS)
    + ". Use null onde o texto nao afirmar o valor. Em 'experiencias' devolva "
    "uma lista de objetos com titulo, empresa, inicio, fim e descricao; use "
    "null em fim quando o cargo for atual. Devolva tambem 'trechos_origem': um "
    "objeto que mapeia cada chave preenchida ao trecho literal do texto que a "
    "originou. Responda apenas com o JSON.\n\n"
)


class ExtractionError(Exception):
    """A extracao nao produziu um registro utilizavel."""


class ManualEntryRequired(ExtractionError):
    """Nao ha caminho automatico: o usuario precisa preencher a mao."""


@dataclass(frozen=True)
class Extraction:
    """Resultado da extracao, antes ou depois da conferencia."""

    extraction_id: str
    resume_id: str
    campos: dict[str, Any]
    trechos_origem: dict[str, str]
    lacunas: list[str]
    provedor: str | None
    modelo: str | None
    correcoes: dict[str, Any] = field(default_factory=dict)
    confirmado_em: str | None = None

    @property
    def confirmada(self) -> bool:
        return self.confirmado_em is not None

    def efetivo(self) -> dict[str, Any]:
        """Valores vigentes: correcao manual prevalece sobre o extraido."""
        return {**self.campos, **self.correcoes}


class ResumeParser:
    """Extrai, reusa, confere e corrige o conteudo estruturado do curriculo."""

    def __init__(self, connection, model_client: ModelClient | None) -> None:
        self._repository = Repository(connection)
        self._client = model_client

    # ----------------------------------------------------------- extracao
    def extract(self, user_id: str, resume_id: str) -> Extraction:
        """Extrai o curriculo, reusando resultado de conteudo ja visto."""
        resumo = self._require_resume(user_id, resume_id)
        digest = resumo["hash_conteudo"]

        reuso = self._find_by_hash(user_id, digest)
        if reuso is not None:
            return self._clone_for(user_id, resume_id, reuso)

        if self._client is None or not self._client.destinations():
            raise ManualEntryRequired(
                "nenhum provedor de modelo disponivel para este usuario; "
                "preencha o perfil manualmente ou cadastre uma credencial"
            )
        self._client.assert_owner(user_id)

        try:
            resposta = self._client.complete(
                "extracao_curriculo", INSTRUCAO + resumo["texto"]
            )
        except DeterministicFallback as exc:
            raise ManualEntryRequired(
                f"nenhum provedor respondeu; preencha o perfil manualmente: {exc}"
            ) from exc
        except ModelError as exc:
            raise ExtractionError(f"a extracao falhou: {exc}") from exc

        campos, trechos = _parse_response(resposta.texto)
        lacunas = [c for c in CAMPOS if not campos.get(c)]
        return self._store(
            user_id=user_id,
            resume_id=resume_id,
            hash_conteudo=digest,
            campos=campos,
            trechos=trechos,
            lacunas=lacunas,
            provedor=resposta.provedor,
            modelo=resposta.modelo,
        )

    def manual(self, user_id: str, resume_id: str, campos: dict) -> Extraction:
        """Cria um registro preenchido a mao, ja confirmado pelo proprio ato."""
        resumo = self._require_resume(user_id, resume_id)
        limpos = {c: campos.get(c) for c in CAMPOS}
        # Confirmar em branco nao protege portao nenhum: cria um perfil sem
        # historico, que o run recusa muito depois e longe daqui. A exigencia e
        # a mesma que `require_for_run` cobra, so que no momento em que o
        # usuario ainda tem o formulario aberto para corrigir.
        if not limpos.get("experiencias"):
            raise ExtractionError(
                "preenchimento manual sem experiencias; e delas que sai toda a "
                "busca, e um perfil sem historico e recusado no inicio do run"
            )
        extraida = self._store(
            user_id=user_id,
            resume_id=resume_id,
            hash_conteudo=resumo["hash_conteudo"],
            campos=limpos,
            trechos={},
            lacunas=[c for c in CAMPOS if not limpos.get(c)],
            provedor=None,
            modelo=None,
        )
        return self.confirm(user_id, extraida.extraction_id)

    # -------------------------------------------------------- conferencia
    def pending(self, user_id: str) -> list[Extraction]:
        """Extracoes esperando conferencia."""
        return [
            _to_extraction(row)
            for row in self._repository.for_user(user_id).select(
                "resume_extractions",
                where="confirmado_em IS NULL",
                order_by="rowid",
            )
        ]

    def extraction(self, user_id: str, extraction_id: str) -> Extraction:
        """Uma extracao do usuario, pelo identificador.

        Existe porque quem grava varias correcoes de uma vez precisa comparar
        cada campo com o valor vigente antes de decidir se ha o que gravar --
        e alcancar isso pelo acessor privado seria a camada web furando a
        fronteira deste modulo.
        """
        return self._require_extraction(user_id, extraction_id)

    def confirmed(self, user_id: str) -> Extraction | None:
        """Extracao confirmada mais recente, que e a que o perfil enxerga."""
        rows = self._repository.for_user(user_id).select(
            "resume_extractions",
            where="confirmado_em IS NOT NULL",
            order_by="rowid DESC",
            limit=1,
        )
        return _to_extraction(rows[0]) if rows else None

    def confirm(self, user_id: str, extraction_id: str) -> Extraction:
        """Abre o portao: a partir daqui o consolidador enxerga esta extracao."""
        alteradas = self._repository.for_user(user_id).update(
            "resume_extractions",
            {"confirmado_em": _stamp()},
            where="extraction_id = ? AND confirmado_em IS NULL",
            params=(extraction_id,),
        )
        if alteradas == 0:
            raise ExtractionError(
                f"extracao {extraction_id} nao encontrada ou ja confirmada"
            )
        return self._require_extraction(user_id, extraction_id)

    def correct(
        self, user_id: str, extraction_id: str, campo: str, valor: Any
    ) -> Extraction:
        """Grava a correcao ao lado do valor extraido, sem sobrescreve-lo."""
        if campo not in CAMPOS:
            raise ExtractionError(
                f"campo {campo!r} desconhecido; esperado um de {list(CAMPOS)}"
            )
        atual = self._require_extraction(user_id, extraction_id)
        correcoes = {**atual.correcoes, campo: valor}
        self._repository.for_user(user_id).update(
            "resume_extractions",
            {"correcoes": json.dumps(correcoes, ensure_ascii=False)},
            where="extraction_id = ?",
            params=(extraction_id,),
        )
        return self._require_extraction(user_id, extraction_id)

    # -------------------------------------------------------------- interno
    def _store(self, **kwargs) -> Extraction:
        extraction_id = str(uuid.uuid4())
        self._repository.for_user(kwargs["user_id"]).insert(
            "resume_extractions",
            {
                "extraction_id": extraction_id,
                "resume_id": kwargs["resume_id"],
                "campos": json.dumps(kwargs["campos"], ensure_ascii=False),
                "trechos_origem": json.dumps(kwargs["trechos"], ensure_ascii=False),
                "lacunas": json.dumps(kwargs["lacunas"], ensure_ascii=False),
                "provedor": kwargs["provedor"],
                "modelo": kwargs["modelo"],
                "hash_conteudo": kwargs["hash_conteudo"],
                "criado_em": _stamp(),
                "correcoes": "{}",
            },
        )
        return self._require_extraction(kwargs["user_id"], extraction_id)

    def _clone_for(self, user_id: str, resume_id: str, fonte: Extraction) -> Extraction:
        """Reaproveita extracao de conteudo identico, sem nova chamada."""
        return self._store(
            user_id=user_id,
            resume_id=resume_id,
            hash_conteudo=self._hash_of(user_id, fonte.extraction_id),
            campos=fonte.campos,
            trechos=fonte.trechos_origem,
            lacunas=fonte.lacunas,
            provedor=fonte.provedor,
            modelo=fonte.modelo,
        )

    def _hash_of(self, user_id: str, extraction_id: str) -> str:
        rows = self._repository.for_user(user_id).select(
            "resume_extractions",
            where="extraction_id = ?",
            params=(extraction_id,),
            columns="hash_conteudo",
        )
        return rows[0]["hash_conteudo"]

    def _find_by_hash(self, user_id: str, digest: str) -> Extraction | None:
        rows = self._repository.for_user(user_id).select(
            "resume_extractions",
            where="hash_conteudo = ?",
            params=(digest,),
            order_by="rowid DESC",
            limit=1,
        )
        return _to_extraction(rows[0]) if rows else None

    def _require_resume(self, user_id: str, resume_id: str):
        rows = self._repository.for_user(user_id).select(
            "resumes", where="resume_id = ?", params=(resume_id,)
        )
        if not rows:
            raise ExtractionError(f"curriculo {resume_id} nao encontrado")
        return rows[0]

    def _require_extraction(self, user_id: str, extraction_id: str) -> Extraction:
        rows = self._repository.for_user(user_id).select(
            "resume_extractions", where="extraction_id = ?", params=(extraction_id,)
        )
        if not rows:
            raise ExtractionError(f"extracao {extraction_id} nao encontrada")
        return _to_extraction(rows[0])


def _parse_response(texto: str) -> tuple[dict, dict]:
    """Le o JSON devolvido, descartando estrutura fora da lista fechada."""
    bruto = (texto or "").strip()
    if bruto.startswith("```"):
        bruto = bruto.split("```")[1]
        if bruto.startswith("json"):
            bruto = bruto[4:]
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio == -1 or fim <= inicio:
        raise ExtractionError("o modelo nao devolveu um objeto JSON")
    try:
        dados = json.loads(bruto[inicio : fim + 1])
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"o JSON devolvido nao pode ser lido: {exc}") from exc
    if not isinstance(dados, dict):
        raise ExtractionError("o modelo devolveu algo que nao e um objeto")
    campos = {c: dados.get(c) for c in CAMPOS}
    trechos_brutos = dados.get("trechos_origem") or {}
    trechos = (
        {k: str(v) for k, v in trechos_brutos.items() if k in CAMPOS}
        if isinstance(trechos_brutos, dict)
        else {}
    )
    return campos, trechos


def _to_extraction(row) -> Extraction:
    return Extraction(
        extraction_id=row["extraction_id"],
        resume_id=row["resume_id"],
        campos=json.loads(row["campos"]),
        trechos_origem=json.loads(row["trechos_origem"]),
        lacunas=json.loads(row["lacunas"]),
        provedor=row["provedor"],
        modelo=row["modelo"],
        correcoes=json.loads(row["correcoes"] or "{}"),
        confirmado_em=row["confirmado_em"],
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
