-- Esquema do banco embarcado.
--
-- Duas regras estruturais governam este arquivo.
--
-- A primeira: toda tabela de dado de usuario carrega `user_id` e apaga em
-- cascata a partir de `users`. E o que faz a exclusao de conta caber numa
-- transacao so, em vez de virar uma sequencia de comandos que alguem precisa
-- lembrar de manter alinhada com o esquema.
--
-- A segunda: `job_descriptions` e deliberadamente compartilhada e nao tem
-- `user_id`. O vinculo entre usuario e vaga vive em `jobs`. E isso que permite
-- apagar tudo de um titular sem destruir um bem comum que nao e dado pessoal
-- dele, e que impede o registro compartilhado de revelar quem coletou o que.

CREATE TABLE IF NOT EXISTS schema_meta (
    chave       TEXT PRIMARY KEY,
    valor       TEXT NOT NULL,
    aplicada_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,
    subject_google   TEXT NOT NULL UNIQUE,
    email            TEXT,
    criado_em        TEXT NOT NULL,
    ultima_sessao_em TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    testemunho_hash TEXT NOT NULL UNIQUE,
    criada_em       TEXT NOT NULL,
    expira_em       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

-- Estado intermediario do fluxo de autorizacao, entre o desvio ao provedor e a
-- volta com o codigo. Vive no banco e nao em cookie porque precisa ser de uso
-- unico: consumir a linha na volta e o que impede reapresentar o mesmo codigo.
CREATE TABLE IF NOT EXISTS auth_flows (
    state         TEXT PRIMARY KEY,
    provedor      TEXT NOT NULL CHECK (provedor IN ('google', 'linkedin')),
    code_verifier TEXT NOT NULL,
    redirect_uri  TEXT NOT NULL,
    user_id       TEXT,
    criado_em     TEXT NOT NULL,
    expira_em     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_flows_expira ON auth_flows(expira_em);

CREATE TABLE IF NOT EXISTS linkedin_connections (
    user_id              TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    campos_identidade    TEXT NOT NULL,
    escopos_concedidos   TEXT NOT NULL,
    campos_indisponiveis TEXT NOT NULL,
    estado               TEXT NOT NULL CHECK (estado IN ('ativa', 'expirada')),
    token_cifrado        BLOB,
    conectada_em         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_credentials (
    credential_id       TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provedor            TEXT NOT NULL,
    chave_cifrada       BLOB NOT NULL,
    chave_de_dado_cifrada BLOB NOT NULL,
    sufixo              TEXT NOT NULL,
    ordem               INTEGER NOT NULL,
    criado_em           TEXT NOT NULL,
    UNIQUE (user_id, provedor)
);
CREATE INDEX IF NOT EXISTS ix_credentials_user_ordem
    ON provider_credentials(user_id, ordem);

CREATE TABLE IF NOT EXISTS resumes (
    resume_id     TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    texto         TEXT NOT NULL,
    origem        TEXT NOT NULL CHECK (origem IN ('gdoc', 'docx', 'pdf')),
    hash_conteudo TEXT NOT NULL,
    importado_em  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_resumes_user ON resumes(user_id);
CREATE INDEX IF NOT EXISTS ix_resumes_hash ON resumes(hash_conteudo);

-- `confirmado_em` nulo e o portao: nenhum run enxerga extracao nao confirmada.
CREATE TABLE IF NOT EXISTS resume_extractions (
    extraction_id  TEXT PRIMARY KEY,
    resume_id      TEXT NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
    user_id        TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    campos         TEXT NOT NULL,
    trechos_origem TEXT NOT NULL,
    lacunas        TEXT NOT NULL,
    provedor       TEXT,
    modelo         TEXT,
    hash_conteudo  TEXT NOT NULL,
    criado_em      TEXT NOT NULL,
    -- Correcoes do usuario, por campo. Ficam ao lado do valor extraido e
    -- nunca por cima dele: preservar os dois e o que permite comparar o que
    -- o modelo leu com o que o humano corrigiu.
    correcoes      TEXT NOT NULL DEFAULT '{}',
    confirmado_em  TEXT
);
CREATE INDEX IF NOT EXISTS ix_extractions_user ON resume_extractions(user_id);
CREATE INDEX IF NOT EXISTS ix_extractions_hash ON resume_extractions(hash_conteudo);

CREATE TABLE IF NOT EXISTS profile_versions (
    version_id        TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    campos            TEXT NOT NULL,
    origem_por_campo  TEXT NOT NULL,
    nivel_inferido    TEXT,
    problemas_higiene TEXT NOT NULL,
    criado_em         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_profile_versions_user
    ON profile_versions(user_id, criado_em DESC);

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    estado            TEXT NOT NULL CHECK (estado IN (
                          'enfileirado', 'em_andamento', 'aguardando_enriquecimento',
                          'concluido', 'interrompido', 'recusado')),
    janela            TEXT NOT NULL CHECK (janela IN ('incremental', 'ampla')),
    solicitado_em     TEXT NOT NULL,
    iniciado_em       TEXT,
    heartbeat_em      TEXT,
    encerrado_em      TEXT,
    buscas            TEXT,
    config_efetiva    TEXT,
    n_brutos          INTEGER NOT NULL DEFAULT 0,
    n_filtrados       INTEGER NOT NULL DEFAULT 0,
    n_novos           INTEGER NOT NULL DEFAULT 0,
    cota_consumida    INTEGER NOT NULL DEFAULT 0,
    cota_esgotada     INTEGER NOT NULL DEFAULT 0,
    bloqueios         INTEGER NOT NULL DEFAULT 0,
    provedor_sintese  TEXT,
    modelo_sintese    TEXT,
    tokens_entrada    INTEGER,
    tokens_saida      INTEGER,
    falha_sintese     TEXT,
    motivo_recusa     TEXT,
    -- Estagios ja concluidos, em JSON. Vive no banco e nao em memoria: e o
    -- que faz um processo morto deixar um run retomavel em vez de um run
    -- que reexecuta requisicao ja paga.
    estagios_concluidos TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS ix_runs_fila ON runs(estado, solicitado_em);
CREATE INDEX IF NOT EXISTS ix_runs_user ON runs(user_id, solicitado_em DESC);

CREATE TABLE IF NOT EXISTS jobs (
    job_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    titulo              TEXT NOT NULL,
    empresa             TEXT,
    url                 TEXT NOT NULL,
    local               TEXT,
    modelo_trabalho     TEXT,
    publicada_em        TEXT,
    flags               TEXT NOT NULL DEFAULT '[]',
    blocker             TEXT,
    estado              TEXT NOT NULL CHECK (estado IN (
                            'novo', 'visto', 'aplicado', 'descartado', 'expirado')),
    primeira_vez_em     TEXT NOT NULL,
    ultima_vez_em       TEXT NOT NULL,
    ausencias_elegiveis INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_jobs_user_estado ON jobs(user_id, estado);

-- Compartilhada entre usuarios e sem `user_id` por decisao de projeto.
CREATE TABLE IF NOT EXISTS job_descriptions (
    job_id                   TEXT PRIMARY KEY,
    texto                    TEXT NOT NULL,
    emails_contato           TEXT NOT NULL DEFAULT '[]',
    candidatos               INTEGER,
    distribuicao_senioridade TEXT,
    coletada_em              TEXT NOT NULL
);

-- Fila propria do enriquecimento. O primeiro trecho do run grava os pedidos
-- aqui e solta o worker; o processo de enriquecimento consome no seu ritmo e,
-- ao esgotar os pedidos de um run, devolve o run a fila para o segundo trecho.
-- Sem esta tabela o run teria de esperar sincronamente, e a espera deliberada
-- do governador -- o intervalo mais longo de todo o run -- ocuparia um worker.
CREATE TABLE IF NOT EXISTS enrichment_requests (
    request_id  TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    job_id      TEXT NOT NULL,
    url         TEXT NOT NULL,
    estado      TEXT NOT NULL CHECK (estado IN ('pendente', 'atendido', 'falhou')),
    criado_em   TEXT NOT NULL,
    atendido_em TEXT,
    UNIQUE (run_id, job_id)
);
CREATE INDEX IF NOT EXISTS ix_enrichment_pendentes
    ON enrichment_requests(estado, criado_em);

CREATE TABLE IF NOT EXISTS scores (
    job_id                     TEXT NOT NULL,
    run_id                     TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    passada                    TEXT NOT NULL CHECK (passada IN ('provisoria', 'final')),
    user_id                    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    score                      INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    componentes                TEXT NOT NULL,
    lacunas                    TEXT NOT NULL DEFAULT '[]',
    diferenciais               TEXT NOT NULL DEFAULT '[]',
    descricao_disponivel       INTEGER NOT NULL DEFAULT 0,
    sinais_sessao_disponiveis  INTEGER NOT NULL DEFAULT 0,
    criado_em                  TEXT NOT NULL,
    PRIMARY KEY (job_id, run_id, passada)
);
CREATE INDEX IF NOT EXISTS ix_scores_user_job ON scores(user_id, job_id, criado_em);

CREATE TABLE IF NOT EXISTS discards (
    run_id  TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    job_id  TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    titulo  TEXT NOT NULL,
    empresa TEXT,
    motivo  TEXT NOT NULL,
    PRIMARY KEY (run_id, job_id)
);

CREATE TABLE IF NOT EXISTS daily_quota (
    user_id   TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    dia       TEXT NOT NULL,
    consumida INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, dia)
);

-- Estado global da contencao: nao pertence a usuario nenhum.
CREATE TABLE IF NOT EXISTS collection_blocks (
    block_id    TEXT PRIMARY KEY,
    ocorrido_em TEXT NOT NULL,
    tentativas  INTEGER NOT NULL,
    liberado_em TEXT
);
CREATE INDEX IF NOT EXISTS ix_blocks_ocorrido ON collection_blocks(ocorrido_em DESC);

-- As duas tabelas abaixo referenciam usuario mas NAO apagam em cascata, e isso e
-- deliberado: o registro de uma exclusao de conta nao pode desaparecer junto com a
-- conta excluida, senao a propria prova de que a exclusao ocorreu se perde. O mesmo
-- vale para a tentativa de acesso cruzado, que e evidencia e nao dado do titular.

CREATE TABLE IF NOT EXISTS data_subject_ops (
    op_id    TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL,
    tipo     TEXT NOT NULL CHECK (tipo IN ('exclusao', 'exportacao')),
    instante TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_denials (
    denial_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    recurso    TEXT NOT NULL,
    instante   TEXT NOT NULL
);

-- Credencial de escopo unico do extrator de insights.
--
-- Existe porque o cookie de sessao e `samesite=lax`, e um POST vindo da pagina
-- do LinkedIn para este servidor e cross-site: o navegador retem o cookie, e a
-- rota que foi construida para ser chamada de la nunca receberia credencial.
--
-- A saida NAO foi afrouxar o cookie. `samesite=none` reabriria CSRF na
-- aplicacao inteira para ganhar uma rota. Um token proprio, mandado em
-- cabecalho, nao sofre SameSite e tambem nao e credencial ambiente: so quem ja
-- o tem consegue envia-lo, que e justamente o que o SameSite protege.
--
-- Guarda o hash e nunca o token. Quem le o banco nao consegue agir como o
-- usuario, e o token so aparece uma vez, na tela que o emite.
CREATE TABLE IF NOT EXISTS extractor_tokens (
    token_hash    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    criado_em     TEXT NOT NULL,
    ultimo_uso_em TEXT
);

CREATE INDEX IF NOT EXISTS ix_extractor_tokens_user ON extractor_tokens(user_id);
