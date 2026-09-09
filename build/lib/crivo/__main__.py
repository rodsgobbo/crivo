"""Ponto de entrada unico: sobe o processo pedido e nada mais.

Quatro processos compoem o sistema e nenhum deles faz o trabalho do outro. Um
comando so, com o modo explicito, torna isso visivel na linha de comando em vez
de escondido em quatro scripts que divergem com o tempo.

O modo `tudo` sobe a face web e os dois processos de trabalho de uma vez. Ele
nao os funde: cada um continua sendo um processo, porque a separacao tem motivo
-- o enriquecedor segura uma trava de instancia unica e a contencao de taxa
depende de haver apenas um. O que o supervisor troca e quem digita os comandos,
e nao o desenho. Exigir tres terminais de quem so quer usar o produto era falta
de acabamento, nao arquitetura.

A configuracao e validada antes de qualquer coisa, inclusive antes de abrir o
banco. Configuracao invalida impede a partida nomeando a chave, em vez de
produzir comportamento padrao silencioso mais adiante.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, load_config
from .logging_filters import install as install_redaction
from .secrets_vault import SecretError, SecretsVault, load_env_file

MODOS = ("web", "runs", "enricher", "schedule", "tudo")

#: Processos que o supervisor sobe ao lado da face web.
ACOMPANHANTES = ("runs", "enricher")

#: Segredos exigidos por modo. O que nao e exigido nao e lido.
SEGREDOS_POR_MODO = {
    "web": ("CRIVO_MASTER_KEY", "CRIVO_DATABASE_URL", "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET"),
    "runs": ("CRIVO_MASTER_KEY", "CRIVO_DATABASE_URL"),
    "enricher": ("CRIVO_DATABASE_URL",),
    "schedule": ("CRIVO_DATABASE_URL",),
    # O supervisor sobe a face web e exige o mesmo que ela: os acompanhantes
    # validam os proprios segredos ao subir, cada um no seu processo.
    "tudo": ("CRIVO_MASTER_KEY", "CRIVO_DATABASE_URL", "GOOGLE_CLIENT_ID",
             "GOOGLE_CLIENT_SECRET"),
}


class StartupError(Exception):
    """O processo nao pode subir."""


def _subir_acompanhantes(args) -> list:  # pragma: no cover - cria processos
    """Sobe os processos de trabalho ao lado da face web.

    Cada um vira um processo proprio, com o mesmo interpretador e o mesmo
    ambiente -- inclusive os segredos que o `.env` ja povoou aqui. Eles herdam a
    saida padrao, entao o log dos tres aparece na mesma janela, que e
    exatamente o que se ganha ao trocar tres terminais por um.
    """
    import subprocess

    subidos = []
    for modo in ACOMPANHANTES:
        comando = [sys.executable, "-m", "crivo", modo, "--config", args.config]
        try:
            subidos.append(subprocess.Popen(comando))
            print(f"[supervisor] {modo} iniciado", file=sys.stderr)
        except OSError as exc:
            # Falhar em subir um acompanhante nao impede a face web: sem eles o
            # produto ainda importa curriculo e consolida perfil, e a propria
            # pagina inicial diz o que deixa de funcionar.
            print(
                f"[supervisor] {modo} nao subiu ({exc}); a face web continua",
                file=sys.stderr,
            )
    return subidos


#: Prazo para o acompanhante sair sozinho depois do Ctrl+C, antes de ser morto.
#: Dez segundos cobrem o `finally` de qualquer laco -- soltar a trava e um
#: UPDATE -- sem prender quem digitou Ctrl+C esperando um processo travado.
PRAZO_DE_SAIDA_S = 10


def _derrubar(processos: list, prazo: float = PRAZO_DE_SAIDA_S) -> None:
    """Encerra os acompanhantes, deixando cada um sair limpo primeiro.

    O Ctrl+C do console ja alcancou os tres processos -- eles pertencem ao mesmo
    grupo -- e cada um esta soltando a propria trava de instancia no `finally`
    do seu laco. Terminar aqui antes de esperar corria contra isso, e no Windows
    `terminate` e `TerminateProcess`: ele nao roda `finally` nenhum. Quando
    ganhava a corrida, o enriquecedor morria segurando a trava.

    O custo nao e teorico. A trava so e considerada abandonada apos quinze
    minutos de silencio, entao subir de novo antes disso era recusado por causa
    de um processo que ja nao existia -- e a saida era apagar a linha no banco
    na mao, sabendo onde ela mora.

    Quem nao sair no prazo continua sendo terminado: o objetivo e nao correr
    contra a saida limpa, e nao esperar para sempre por um processo travado.
    """
    import time

    restantes = [p for p in processos if p.poll() is None]
    limite = time.monotonic() + prazo
    for processo in restantes:
        try:
            processo.wait(timeout=max(0.0, limite - time.monotonic()))
        except Exception:
            # Ainda vivo quando o prazo comum acabou. Os proximos passos
            # decidem o que fazer com ele.
            pass

    teimosos = [p for p in restantes if p.poll() is None]
    for processo in teimosos:
        processo.terminate()
    for processo in teimosos:
        try:
            processo.wait(timeout=5)
        except Exception:
            processo.kill()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="crivo", description=__doc__)
    parser.add_argument("modo", choices=MODOS, help="qual processo subir")
    parser.add_argument("--config", default="config/default.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--once", action="store_true", help="um ciclo e encerra")
    parser.add_argument(
        "--intervalo", type=float, default=5.0,
        help="segundos de espera do processo de runs quando a fila esta vazia",
    )
    return parser.parse_args(argv)


def prepare(modo: str, caminho_config: str, environ=None):
    """Valida configuracao e segredos, e instala a redacao de log."""
    if modo not in MODOS:
        raise StartupError(
            f"modo {modo!r} desconhecido; esperado um de {list(MODOS)}"
        )
    try:
        config = load_config(caminho_config)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc

    # Antes do cofre: ele so le o ambiente, e alguem precisa povoa-lo.
    if environ is None:
        load_env_file()
    cofre = SecretsVault(environ)
    install_redaction(cofre)
    faltando = cofre.missing(list(SEGREDOS_POR_MODO[modo]))
    if faltando:
        raise StartupError(
            f"modo {modo}: variaveis de ambiente ausentes {faltando}; "
            "veja .env.example"
        )
    return config, cofre


#: Codigo de saida convencional para encerramento por sinal de interrupcao.
#: 128 mais o numero do sinal, e SIGINT e 2.
SAIDA_POR_INTERRUPCAO = 130


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada. Ctrl+C encerra sem parecer defeito.

    Sem este envoltorio, parar o servidor imprimia um traceback de
    `KeyboardInterrupt` -- e, no modo `tudo`, tres deles entrelacados na mesma
    janela, porque os processos escrevem juntos. O encerramento estava correto,
    e as travas de instancia sao liberadas pelos `finally` de cada laco; o que
    estava errado era so o que a tela dizia.
    """
    args = parse_args(argv)
    try:
        return _executar(args)
    except KeyboardInterrupt:
        # Uma linha, nomeando quem parou: no modo `tudo` sao tres processos
        # escrevendo na mesma janela, e "encerrado" sem dono nao diz qual.
        # Nada alem disso -- os `finally` dos lacos ja soltaram as travas, e
        # quem digitou Ctrl+C sabe o que fez.
        print(f"\n[{args.modo}] encerrado.", file=sys.stderr)
        return SAIDA_POR_INTERRUPCAO


def _executar(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config, cofre = prepare(args.modo, args.config)
    except StartupError as exc:
        print(f"nao foi possivel subir: {exc}", file=sys.stderr)
        return 2

    from .store.migrations import ThreadLocalDatabase

    banco = ThreadLocalDatabase(cofre.require("CRIVO_DATABASE_URL"))

    acompanhantes: list = []
    if args.modo == "tudo":  # pragma: no cover - sobe processos
        # Um comando, e nao um processo. Os tres continuam separados porque a
        # separacao tem motivo: o enriquecedor segura uma trava de instancia
        # unica, e a contencao de taxa depende de haver apenas um. O que muda
        # aqui e so quem digita os comandos.
        acompanhantes = _subir_acompanhantes(args)
        args.modo = "web"

    if args.modo == "web":  # pragma: no cover - sobe servidor
        import uvicorn

        from .providers.client import LiteLLMRouter
        from .providers.vault import EnvelopeCipher
        from .web.app import create_app
        from .web.auth import GoogleIdentityProvider
        from .web.linkedin import OfficialLinkedInProvider

        # A conexao LinkedIn e opcional: sem os segredos dela o processo sobe e
        # a origem simplesmente nao existe. O que nao pode acontecer e subir com
        # a porta declarada e nada atras dela -- foi assim que uma rota viva
        # respondeu 500 com AttributeError em vez de dizer o que faltava.
        linkedin = None
        if not cofre.missing(["LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"]):
            linkedin = OfficialLinkedInProvider(
                cofre.require("LINKEDIN_CLIENT_ID"),
                cofre.require("LINKEDIN_CLIENT_SECRET"),
            )

        app = create_app(
            config,
            banco,
            identity_provider=GoogleIdentityProvider(
                cofre.require("GOOGLE_CLIENT_ID"),
                cofre.require("GOOGLE_CLIENT_SECRET"),
            ),
            linkedin_provider=linkedin,
            cipher=EnvelopeCipher(cofre.require("CRIVO_MASTER_KEY")),
            # Sem roteador o cliente de modelo nasce nulo, e extracao
            # automatica e sintese ficam impossiveis por construcao -- o
            # produto inteiro cai no modo manual mesmo com credencial
            # cadastrada. A fabrica e injetada aqui, e nao dentro da
            # aplicacao, para que o processo web continue subindo sem rede
            # nos testes.
            router_factory=LiteLLMRouter,
        )
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            # Quem subiu os acompanhantes tem de derruba-los. Sem isto, um
            # Ctrl+C deixaria o enriquecedor vivo segurando a trava de
            # instancia unica, e a proxima partida se recusaria a subir.
            _derrubar(acompanhantes)
        return 0

    if args.modo == "runs":  # pragma: no cover - laco longo
        from .pipeline.sources.guest import MultiPortalSource
        from .pipeline.stages import build_stages
        from .providers.client import LiteLLMRouter, ModelClient
        from .providers.registry import ProviderRegistry, load_providers
        from .providers.vault import CredentialVault, EnvelopeCipher
        from .worker.runner import Runner

        conexao = banco()
        # A sintese e opcional e depende de credencial do proprio usuario. A
        # fabrica devolve `None` quando nao ha chave cadastrada, e o estagio
        # entao segue em modo deterministico em vez de derrubar o run.
        def cliente_de_modelo(user_id: str):
            if cofre.missing(["CRIVO_MASTER_KEY"]):
                return None
            registro = ProviderRegistry(load_providers())
            return ModelClient(
                user_id,
                CredentialVault(
                    conexao,
                    EnvelopeCipher(cofre.require("CRIVO_MASTER_KEY")),
                    registro,
                ),
                registro,
                LiteLLMRouter(),
                config.synthesis.limite_caracteres_texto_externo,
            )

        from .store.locks import CHAVE_DO_EXECUTOR, AlreadyRunning, InstanceLock
        from .store.repository import Repository
        from .worker.queue import RunQueue

        # A mesma trava do enriquecedor, e pelo mesmo motivo. Dois executores
        # nao entregam nada mais rapido: eles disputam a capacidade de coleta,
        # duplicam requisicao paga e -- pior -- `reclaim_abandoned` logo abaixo
        # supoe que quem esta `em_andamento` na partida so pode ser um morto.
        # Com dois processos essa suposicao e falsa, e o segundo a subir devolve
        # a fila um run que o primeiro esta executando neste instante.
        trava = InstanceLock(Repository(conexao), CHAVE_DO_EXECUTOR, "runs")
        try:
            trava.acquire()
        except AlreadyRunning as erro:
            print(f"[runs] {erro}", file=sys.stderr)
            return 1

        # Reiniciar o servidor e coisa normal e nao pode custar seis horas de
        # bloqueio. Quem morreu no meio de um estagio deixou o run preso em
        # andamento, e so este processo executa estagios -- entao ele e quem
        # tem autoridade para devolve-lo a fila.
        recuperados = RunQueue(conexao).reclaim_abandoned()
        if recuperados:
            print(
                f"[runs] {len(recuperados)} run(s) sem dono devolvido(s) a fila: "
                + ", ".join(r[:8] for r in recuperados),
                file=sys.stderr,
            )

        runner = Runner(
            conexao,
            config,
            stages=build_stages(
                conexao, config, MultiPortalSource(), cliente_de_modelo
            ),
            keepalive=trava.heartbeat,
        )
        # Fila vazia nao e fim do trabalho: e espera. Encerrar no primeiro
        # vazio fazia o processo morrer em segundos, e quem enfileirasse uma
        # busca depois nunca seria atendido -- o sintoma era rodar o comando e
        # ver saida nenhuma. Com `--once` o comportamento antigo continua, que
        # e o que serve a script e a diagnostico.
        import time

        try:
            while True:
                if runner.run_once() is not None:
                    # Um run acabou de passar por aqui: a trava precisa dizer
                    # que ha alguem vivo, senao um estagio longo a deixa vencer
                    # e a partida seguinte a toma achando este processo morto.
                    trava.heartbeat()
                    continue
                trava.heartbeat()
                if args.once:
                    break
                time.sleep(args.intervalo)
        finally:
            trava.release()
        return 0

    if args.modo == "enricher":  # pragma: no cover - laco longo
        from .pipeline.sources.guest import MultiPortalSource
        from .worker.enricher_process import EnricherProcess

        processo = EnricherProcess(banco(), config, MultiPortalSource())
        processo.serve_forever(ciclos=1 if args.once else None)
        return 0

    from .scheduler import Scheduler

    resultado = Scheduler(banco(), config).run_cycle()
    print(
        f"ciclo: {len(resultado.enfileirados)} enfileirados, "
        f"{len(resultado.recusados)} recusados, "
        f"{len(resultado.interrompidos)} interrompidos"
        + (f", adiado: {resultado.motivo}" if resultado.adiado_por_bloqueio else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
