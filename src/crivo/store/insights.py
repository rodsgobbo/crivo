"""Insights de concorrencia que so a conta do proprio usuario enxerga.

O LinkedIn Premium mostra, na pagina de uma vaga, dados que a rota publica nao
entrega: a contagem de candidatos, a posicao do usuario em relacao a eles e a
distribuicao de senioridade de quem se candidatou. O desenho original atribuia
esses sinais a uma sessao de conta operacional, que foi removida por transferir
o risco para uma credencial guardada.

Este modulo recebe os mesmos dados por outro caminho: eles chegam ja extraidos,
de um trecho de codigo que roda no navegador do proprio usuario, numa pagina que
ele esta vendo logado. Nenhuma credencial atravessa esta fronteira -- o sistema
recebe o resultado da leitura, nunca o meio de fazer a leitura. E a diferenca
que importa: um cookie guardado aqui poderia ser usado sem o usuario; um numero
guardado aqui nao pode.

Por isso a validacao e estrita. O dado vem de fora, de uma pagina que o sistema
nao controla, e a unica coisa que impede uma leitura errada de virar um score
errado e a recusa explicita do que nao tem forma de insight.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .repository import Repository

#: Rotulos que o sistema aceita gravar como sinal de vaga. Qualquer outro e
#: descartado: sinal desconhecido nao vira bonus, e uma pagina que mudou de
#: forma nao pode inventar categoria nova dentro do scorer.
SINAIS_ACEITOS = frozenset(
    {"top_applicant", "early_applicant", "muitos_candidatos", "premium_insight"}
)

#: Teto de sanidade para a contagem. Um numero acima disso e leitura errada, nao
#: vaga popular.
MAXIMO_DE_CANDIDATOS = 1_000_000


class InsightError(ValueError):
    """Payload de insight ausente, malformado ou de vaga que nao e do usuario."""


class InsightStore:
    """Grava insights de concorrencia sobre uma vaga do usuario."""

    def __init__(self, connection) -> None:
        self._repository = Repository(connection)

    def record(self, user_id: str, job_id: str, payload: dict) -> dict:
        """Valida e grava. Devolve o que de fato foi gravado."""
        if not self._pertence(user_id, job_id):
            # A vaga que voce esta olhando quase nunca e uma que o crivo achou.
            # Medido no banco real: das doze empresas visiveis numa varredura,
            # UMA tinha vaga coletada. As buscas do planejador sao focadas no
            # Brasil e a navegacao do usuario nao e, entao a sobreposicao e
            # estruturalmente proxima de zero -- e recusar a vaga desconhecida
            # fazia a varredura inteira gravar nada, sem erro nenhum.
            #
            # Criar aqui nao afrouxa o isolamento: a vaga nasce do usuario que
            # esta autenticado e para ele. O que ela nao tem e procedencia de
            # busca, e por isso a origem fica gravada como `extensao` -- quem
            # ler o relatorio precisa saber que esta veio da navegacao e nao do
            # planejador.
            if not self._criar(user_id, job_id, payload.get("vaga")):
                raise InsightError(
                    f"vaga {job_id} nao existe para este usuario e o envio nao "
                    "trouxe titulo e endereco para cria-la"
                )

        candidatos = _inteiro(payload.get("candidatos"))
        senioridade = _distribuicao(payload.get("senioridade"))
        sinais = sorted(
            {
                s for s in (payload.get("sinais") or [])
                if isinstance(s, str) and s in SINAIS_ACEITOS
            }
        )

        if candidatos is None and not senioridade and not sinais:
            raise InsightError(
                "nenhum insight reconhecivel no envio; a pagina pode ter mudado "
                "de forma ou a vaga pode nao exibir esses dados"
            )

        gravado: dict = {}
        if candidatos is not None or senioridade:
            self._grava_concorrencia(job_id, candidatos, senioridade)
            if candidatos is not None:
                gravado["candidatos"] = candidatos
            if senioridade:
                gravado["senioridade"] = senioridade
        if sinais:
            self._grava_sinais(user_id, job_id, sinais)
            gravado["sinais"] = sinais
        return gravado

    # ------------------------------------------------------------ interno
    #: Origem gravada na coluna `busca` das vagas que entraram pela navegacao.
    #: A coleta grava ali o termo que trouxe a vaga; aqui nao houve termo, e
    #: inventar um faria o relatorio de rendimento das buscas mentir.
    ORIGEM = "extensao"

    def _criar(self, user_id: str, job_id: str, vaga) -> bool:
        """Cria a vaga a partir do card, se o envio trouxe o minimo.

        O minimo e titulo e endereco. Sem titulo nao ha o que pontuar -- o
        pre-filtro e o score leem o titulo antes de qualquer outra coisa --, e
        sem endereco o usuario nao consegue voltar para a vaga a partir do
        relatorio, que e a unica acao que ele quer tomar ali.

        `estado` nasce `novo` como na coleta: e o proximo run que a pontua, e
        ate la ela e uma vaga conhecida sem nota, exatamente como uma recem
        coletada entre a coleta e a pontuacao.
        """
        if not isinstance(vaga, dict):
            return False
        titulo = str(vaga.get("titulo") or "").strip()[:300]
        url = str(vaga.get("url") or "").strip()[:600]
        if not titulo or not url:
            return False

        agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._repository.for_user(user_id).insert(
            "jobs",
            {
                "job_id": job_id,
                "titulo": titulo,
                "empresa": (str(vaga.get("empresa") or "").strip() or None),
                "url": url,
                "local": (str(vaga.get("local") or "").strip() or None),
                "modelo_trabalho": None,
                "publicada_em": None,
                "flags": "[]",
                "estado": "novo",
                "primeira_vez_em": agora,
                "ultima_vez_em": agora,
                "busca": self.ORIGEM,
            },
        )
        return True

    def _pertence(self, user_id: str, job_id: str) -> bool:
        return bool(
            self._repository.for_user(user_id).select(
                "jobs", where="job_id = ?", params=(job_id,), columns="job_id"
            )
        )

    def _grava_concorrencia(
        self, job_id: str, candidatos: int | None, senioridade: dict
    ) -> None:
        """Escreve no registro compartilhado, sem apagar o que ja estava la.

        A descricao e compartilhada entre usuarios, entao um envio que nao traz
        senioridade nao pode zerar a senioridade que outro envio trouxe.
        """
        campos, valores = [], []
        if candidatos is not None:
            campos.append("candidatos = ?")
            valores.append(candidatos)
        if senioridade:
            campos.append("distribuicao_senioridade = ?")
            valores.append(json.dumps(senioridade, ensure_ascii=False))
        if not campos:
            return

        # A linha pode nao existir, e ate aqui isso perdia o dado em silencio.
        # Uma vaga que entrou pela navegacao nunca passou pelo enriquecimento,
        # entao nao ha descricao dela -- o UPDATE acertava zero linhas enquanto
        # a resposta da rota dizia `{"candidatos": 100}`, afirmando uma gravacao
        # que nao aconteceu.
        #
        # `texto` e obrigatorio no esquema e aqui nao ha texto nenhum: a marca
        # vazia diz que a linha existe pela concorrencia e nao pela descricao, e
        # o enriquecimento a preenche depois sem apagar estes numeros.
        self._repository.execute(
            "INSERT INTO job_descriptions (job_id, texto, coletada_em) "
            "VALUES (?, '', ?) ON CONFLICT(job_id) DO NOTHING",
            (job_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        valores.append(job_id)
        self._repository.execute(
            f"UPDATE job_descriptions SET {', '.join(campos)} WHERE job_id = ?",
            tuple(valores),
        )

    def _grava_sinais(self, user_id: str, job_id: str, sinais: list[str]) -> None:
        """Une aos sinais que a coleta ja tinha, em vez de substitui-los."""
        escopo = self._repository.for_user(user_id)
        linhas = escopo.select(
            "jobs", where="job_id = ?", params=(job_id,), columns="flags"
        )
        atuais = json.loads(linhas[0]["flags"] or "[]") if linhas else []
        unidos = sorted(set(atuais) | set(sinais))
        escopo.update(
            "jobs",
            {"flags": json.dumps(unidos, ensure_ascii=False)},
            where="job_id = ?",
            params=(job_id,),
        )


def _inteiro(valor) -> int | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    if numero < 0 or numero > MAXIMO_DE_CANDIDATOS:
        return None
    return numero


def _distribuicao(valor) -> dict:
    """Mantem apenas pares rotulo/contagem legiveis."""
    if not isinstance(valor, dict):
        return {}
    limpo = {}
    for rotulo, quantidade in valor.items():
        if not isinstance(rotulo, str) or not rotulo.strip():
            continue
        numero = _inteiro(quantidade)
        if numero is not None:
            limpo[rotulo.strip()[:60]] = numero
    return limpo
