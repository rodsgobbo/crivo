"""Travas de instancia unica, gravadas no proprio banco.

A chave vive aqui, e nao no processo que a adquire, porque quem escreve e quem
le sao processos diferentes. O enriquecedor adquire a trava; a face web apenas
pergunta se ela existe, para dizer ao usuario se aquele processo esta de pe.

Deixar a constante no modulo do enriquecedor obrigaria a face web a importar o
processo de trabalho inteiro para ler uma string -- uma dependencia entre
processos que o desenho separa de proposito. O teste de fronteira existente so
barrava o executor de estagios, entao esse import teria passado; passar num
teste nao e o mesmo que respeitar a fronteira que ele guarda.

A mecanica da trava tambem mora aqui, e nao no enriquecedor, porque o processo
de runs precisa da mesma. Dois consumidores da mesma fila nao entregam nada
mais rapido: eles disputam a capacidade de coleta e duplicam requisicao paga.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

#: Trava do processo unico de enriquecimento. A contencao de taxa depende de
#: haver apenas um, e a unicidade e verificada no banco em vez de combinada.
CHAVE_DO_ENRIQUECEDOR = "enricher_em_execucao"

#: Trava do processo unico de runs.
CHAVE_DO_EXECUTOR = "runs_em_execucao"

#: Silencio a partir do qual a trava e considerada abandonada.
#:
#: Precisa ser maior que o trecho mais longo que um detentor pode passar sem
#: renovar. No enriquecedor esse trecho e um ciclo cheio -- vinte e cinco
#: pedidos separados por ate vinte segundos, cerca de oito minutos -- e a espera
#: pos-bloqueio, que dura uma hora e por isso e atravessada em fatias.
SILENCIO_ATE_ABANDONO_S = 15 * 60


class AlreadyRunning(Exception):
    """Ja existe um processo vivo segurando esta trava."""


def vencida(carimbo: str | None, agora: datetime | None = None) -> bool:
    """A trava passou tempo demais sem dar sinal de vida?

    Carimbo ilegivel conta como vencido: uma trava que ninguem consegue
    interpretar bloquearia a partida para sempre, e o custo de assumi-la
    indevidamente e menor do que o de um processo que nunca sobe.
    """
    if not carimbo:
        return True
    try:
        marcado = datetime.fromisoformat(str(carimbo))
    except ValueError:
        return True
    if marcado.tzinfo is None:
        marcado = marcado.replace(tzinfo=timezone.utc)
    referencia = agora or datetime.now(timezone.utc)
    return referencia - marcado > timedelta(seconds=SILENCIO_ATE_ABANDONO_S)


def ativo(repository, chave: str) -> bool:
    """Ha um detentor vivo desta trava nesta instalacao?"""
    linhas = repository.select("schema_meta", where="chave = ?", params=(chave,))
    return bool(linhas) and not vencida(linhas[0]["aplicada_em"])


def enricher_ativo(repository) -> bool:
    """Ha um processo de enriquecimento de pe nesta instalacao?"""
    return ativo(repository, CHAVE_DO_ENRIQUECEDOR)


class InstanceLock:
    """Trava de instancia unica com sinal de vida.

    A existencia da linha nao basta como criterio. Um processo derrubado a
    forca -- ou junto com o terminal -- deixa a marca para tras, e sem um sinal
    de vida a partida seguinte se recusa a subir citando um concorrente que nao
    existe. A saida, entao, e apagar a linha na mao, sabendo onde ela mora.

    O criterio e silencio, e nao identificador de processo: perguntar ao sistema
    operacional se um PID vive tem resposta diferente em cada plataforma, e no
    Windows o caminho mais obvio mataria o processo em vez de consulta-lo.
    """

    def __init__(self, repository, chave: str, rotulo: str, logger=None) -> None:
        self._repository = repository
        self._chave = chave
        self._rotulo = rotulo
        self._logger = logger

    def acquire(self) -> None:
        linhas = self._repository.select(
            "schema_meta", where="chave = ?", params=(self._chave,)
        )
        if linhas and not vencida(linhas[0]["aplicada_em"]):
            raise AlreadyRunning(
                f"ja ha um processo de {self._rotulo} ativo; a contencao de "
                "taxa depende de haver apenas um. Encerre o outro antes de "
                "subir este"
            )
        if linhas:
            if self._logger:
                self._logger.warning(
                    "trava de %s sem sinal de vida desde %s; assumindo que o "
                    "processo anterior morreu e tomando o lugar dele",
                    self._rotulo, linhas[0]["aplicada_em"],
                )
            self.release()
        self._repository.insert(
            "schema_meta",
            {"chave": self._chave, "valor": "1", "aplicada_em": _stamp()},
        )

    def heartbeat(self) -> None:
        """Renova o sinal de vida."""
        self._repository.execute(
            "UPDATE schema_meta SET aplicada_em = ? WHERE chave = ?",
            (_stamp(), self._chave),
        )

    def release(self) -> None:
        self._repository.execute(
            "DELETE FROM schema_meta WHERE chave = ?", (self._chave,)
        )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
