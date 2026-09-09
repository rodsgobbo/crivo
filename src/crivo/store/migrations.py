"""Abertura do banco, versionamento e migracao ordenada.

A abertura e o unico ponto por onde uma conexao nasce. Ela liga integridade
referencial, poe o banco em escrita antecipada, compara a versao gravada no
arquivo com a esperada pelo codigo e aplica os passos pendentes antes de
devolver a conexao. Um arquivo mais novo que o codigo faz a abertura falhar em
vez de operar sobre estrutura desconhecida.

Sem isto, uma ferramenta feita para rodar todo dia por meses perderia o
historico de score na primeira mudanca de coluna, e esse historico e o unico
indicador de que mexer no perfil adiantou.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Versao que este codigo sabe operar. Sobe a cada passo acrescentado abaixo.
TARGET_VERSION = 9

#: Passos de migracao por versao de destino. Cada entrada tem o passo direto e o
#: inverso; o inverso existe para que a reversao seja declarada e nao inventada
#: na hora, mesmo que a recuperacao normal seja restaurar a copia do arquivo.
def _v2_janela_em_horas(connection: sqlite3.Connection) -> None:
    """Guarda a janela de publicacao escolhida, em horas, junto do run.

    Ate aqui a janela era so um rotulo -- `incremental` ou `ampla` -- e as horas
    saiam da configuracao no momento da coleta. Isso impedia o usuario de
    escolher o alcance da busca, e tambem fazia um run antigo ser lido com a
    configuracao de hoje, e nao com a que valia quando ele rodou.

    Nulo continua valido e significa "use a configuracao", que e como todo run
    anterior a esta versao foi executado.
    """
    connection.execute("ALTER TABLE runs ADD COLUMN janela_horas INTEGER")


def _v3_buscas_do_usuario(connection: sqlite3.Connection) -> None:
    """Buscas escritas pelo proprio usuario, somadas as que o perfil gera.

    O vocabulario de cargo muda mais rapido que qualquer lista de dominio, e
    quem procura sabe coisas do proprio mercado que o perfil nao diz. Sem este
    lugar, a unica forma de procurar "Head of SRE" era editar um TOML do
    repositorio -- o que nao e uma opcao para quem so quer usar o produto.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS buscas_do_usuario (
            user_id   TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            texto     TEXT NOT NULL,
            criado_em TEXT NOT NULL,
            PRIMARY KEY (user_id, texto)
        )
        """
    )


def _v4_texto_da_sintese(connection: sqlite3.Connection) -> None:
    """Guarda o texto da sintese, e nao apenas quanto ela custou.

    O registro do run gravava provedor, modelo e tokens -- a proveniencia e o
    preco -- e descartava o unico produto daquilo: a leitura em si. Um run real
    gastou 2.665 tokens de entrada e 3.640 de saida, e o relatorio abriu sem
    nenhuma leitura estrategica, porque nao havia de onde le-la.

    A coluna e anulavel: modo deterministico e falha de sintese continuam
    produzindo run valido, e a ausencia de texto e um estado legitimo.
    """
    connection.execute("ALTER TABLE runs ADD COLUMN sintese TEXT")


def _v5_cancelamento_pedido(connection: sqlite3.Connection) -> None:
    """Marca o pedido de cancelamento de um run em curso.

    O cancelamento nao pode ser a mudanca direta do estado do run: quem clica
    esta na face web, e quem executa a coleta e outro processo, no meio de uma
    sequencia de requisicoes a origem. Mudar o estado por baixo dele deixaria
    requisicao pela metade e cards coletados sem dono.

    Entao o clique grava um pedido, e o processo de runs o atende no ponto onde
    parar e barato -- entre duas buscas. A coluna e o unico canal entre os dois,
    e ela guarda o instante do pedido em vez de um booleano porque a diferenca
    entre "pedido agora" e "pedido ha dez minutos e ninguem atendeu" e
    exatamente o que diz se o processo executor ainda esta vivo.
    """
    connection.execute("ALTER TABLE runs ADD COLUMN cancelado_em TEXT")


def _v6_busca_de_origem(connection: sqlite3.Connection) -> None:
    """Guarda qual busca trouxe cada vaga.

    O coletor recebia esse dado da origem e o descartava na gravacao. Sem ele,
    "por que esta vaga apareceu?" so tinha resposta lendo o codigo do
    planejador -- e, pior, nao havia como atribuir resultado ruim a termo ruim.
    Uma correcao no planejador ficava sem como ser medida: dava para ver que o
    relatorio mudou, nao que mudou por causa dela.

    Guarda a busca da primeira vez que a vaga foi vista, e nao a da ultima. A
    pergunta que a coluna responde e de origem, e reaparecimento nao muda de
    onde algo veio.

    Nulo continua valido: e como esta toda vaga coletada antes desta versao.
    """
    connection.execute("ALTER TABLE jobs ADD COLUMN busca TEXT")


def _v7_recencia_da_descricao(connection: sqlite3.Connection) -> None:
    """Guarda a recencia que o anuncio declara, em vez de descarta-la.

    O card da busca chega sem data: a origem marca anuncio recente com outra
    classe de marcacao, e a biblioteca de coleta so procura a antiga. Numa
    janela de 24 horas isso significa que **nenhuma** vaga tem data -- e sem
    data nao ha como conferir se a janela pedida foi respeitada.

    A pagina do anuncio traz a recencia por extenso ("ha 3 semanas"), e o
    parser ja a extraia. O enriquecedor entao a jogava fora, gravando texto,
    contato e candidatos e perdendo justamente o campo que responde "esta busca
    trouxe o que eu pedi?". A requisicao ja foi paga; o que faltava era guardar.

    Fica como texto e nao como data porque e o que o anuncio afirma. A conversao
    para data e derivada e mora no codigo, onde pode ser corrigida sem migracao.
    """
    connection.execute("ALTER TABLE job_descriptions ADD COLUMN publicada_ha TEXT")


def _v8_julgamento_do_modelo(connection: sqlite3.Connection) -> None:
    """Guarda a segunda leitura que o modelo faz das melhores vagas.

    O calculo deterministico erra numa coisa e erra caro: o componente de
    competencias e uma proporcao, entao anuncio vago -- que pede pouco -- pontua
    mais que vaga exigente. Num run real um "Banco de Talentos" ficou acima de
    um "Especialista de SRE".

    As colunas ficam ao lado do score e nao no lugar dele. A ordem
    deterministica continua existindo e sendo reprodutivel; a releitura e uma
    camada por cima, que some quando nao ha credencial de modelo. Sobrescrever o
    score apagaria a unica ordem que duas leituras do mesmo run garantem ser
    igual.

    Nulo e o estado normal: modo deterministico, ausencia de credencial e
    resposta ilegivel produzem run valido sem releitura.
    """
    connection.execute("ALTER TABLE scores ADD COLUMN nota_do_modelo INTEGER")
    connection.execute("ALTER TABLE scores ADD COLUMN motivo_do_modelo TEXT")


def _v9_token_do_extrator(connection: sqlite3.Connection) -> None:
    """Credencial de escopo unico para o extrator que roda no navegador.

    A rota de insights foi construida com cabecalhos CORS e uma origem liberada
    para ser chamada de dentro da pagina do LinkedIn -- mas a credencial que ela
    exige nunca chegaria la. O cookie de sessao e `samesite=lax`, e um POST
    cross-site nao o carrega; a guarda so lia cookie, entao toda varredura
    voltaria 401. O recurso existia inteiro e era inalcancavel.

    Afrouxar o cookie para `samesite=none` resolveria a rota e abriria CSRF em
    todas as outras. Este token nao: viaja em cabecalho, que SameSite nao
    governa, e vale so para escrever insight. Vazado, ele nao le relatorio,
    perfil nem credencial de provedor -- e a mesma linha que levou o projeto a
    recusar uma sessao de conta operacional guardada.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS extractor_tokens (
            token_hash    TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            criado_em     TEXT NOT NULL,
            ultimo_uso_em TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_extractor_tokens_user "
        "ON extractor_tokens(user_id)"
    )


MIGRATIONS: dict[int, tuple[Callable[[sqlite3.Connection], None], str]] = {
    9: (_v9_token_do_extrator, "DROP TABLE extractor_tokens"),
    8: (
        _v8_julgamento_do_modelo,
        "ALTER TABLE scores DROP COLUMN nota_do_modelo; "
        "ALTER TABLE scores DROP COLUMN motivo_do_modelo",
    ),
    7: (
        _v7_recencia_da_descricao,
        "ALTER TABLE job_descriptions DROP COLUMN publicada_ha",
    ),
    6: (_v6_busca_de_origem, "ALTER TABLE jobs DROP COLUMN busca"),
    5: (_v5_cancelamento_pedido, "ALTER TABLE runs DROP COLUMN cancelado_em"),
    4: (_v4_texto_da_sintese, "ALTER TABLE runs DROP COLUMN sintese"),
    3: (_v3_buscas_do_usuario, "DROP TABLE buscas_do_usuario"),
    2: (
        _v2_janela_em_horas,
        # O inverso e declarado e nao inventado na hora. O SQLite so passou a
        # aceitar DROP COLUMN na versao 3.35; abaixo dela a reversao e
        # restaurar a copia do arquivo, e dizer isso e melhor do que oferecer
        # um comando que falha na metade dos ambientes.
        "ALTER TABLE runs DROP COLUMN janela_horas",
    ),
}


class SchemaError(Exception):
    """Banco em versao incompativel com este codigo."""


class StoreError(Exception):
    """Falha de gravacao ou de leitura no banco."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_version(connection: sqlite3.Connection) -> int:
    """Devolve a versao gravada no arquivo, ou 0 quando o banco esta vazio."""
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
    ).fetchone()
    if tables is None:
        return 0
    row = connection.execute(
        "SELECT valor FROM schema_meta WHERE chave = 'versao_esquema'"
    ).fetchone()
    return int(row[0]) if row else 0


def _write_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        "INSERT INTO schema_meta (chave, valor, aplicada_em) VALUES (?, ?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
        "aplicada_em = excluded.aplicada_em",
        ("versao_esquema", str(version), _now()),
    )


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Leva o banco ate `TARGET_VERSION` e devolve a versao resultante."""
    current = read_version(connection)
    if current > TARGET_VERSION:
        raise SchemaError(
            f"esquema do banco na versao {current}, superior a versao "
            f"{TARGET_VERSION} conhecida por este codigo; "
            "atualize o codigo ou restaure a copia anterior do arquivo"
        )
    if current == TARGET_VERSION:
        return current

    with connection:
        if current == 0:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            current = 1
            _write_version(connection, current)
        for version in range(current + 1, TARGET_VERSION + 1):
            step, _inverse = MIGRATIONS[version]
            step(connection)
            current = version
            _write_version(connection, current)
    return current


def _connect(path: Path) -> sqlite3.Connection:
    """Abre uma conexao com os pragmas do projeto, sem tocar em migracao."""
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    # Integridade referencial vem desligada por padrao no SQLite; sem ela a
    # exclusao em cascata que sustenta os direitos do titular nao acontece.
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def open_database(path: Path | str) -> sqlite3.Connection:
    """Abre o banco, aplica migracoes pendentes e devolve a conexao pronta."""
    path = Path(path)
    if path.parent != Path("") and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect(path)
    try:
        apply_migrations(connection)
    except SchemaError:
        connection.close()
        raise
    return connection


class ThreadLocalDatabase:
    """Entrega uma conexao por thread sobre o mesmo arquivo.

    Uma conexao do SQLite pertence a thread que a criou. O processo web atende
    requisicoes sincronas num conjunto de threads, entao compartilhar uma unica
    conexao entre elas falha assim que a segunda thread toca o banco. Este
    objeto resolve isso abrindo uma conexao por thread e aplicando as migracoes
    apenas uma vez, na primeira abertura.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._local = threading.local()
        self._migrated = False
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def __call__(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            return connection
        with self._lock:
            if not self._migrated:
                first = open_database(self._path)
                self._migrated = True
                self._local.connection = first
                return first
        connection = _connect(self._path)
        self._local.connection = connection
        return connection

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None
