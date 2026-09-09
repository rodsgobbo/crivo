import pytest

from crivo.config import load_config
from crivo.providers.client import Completion, DeterministicFallback, ModelClient
from crivo.providers.registry import ProviderRegistry, load_providers
from crivo.providers.vault import CredentialVault, EnvelopeCipher, generate_master_key
from crivo.store.migrations import open_database
from crivo.store.repository import Repository
from crivo.synthesis import grounding, prompt
from crivo.synthesis.synthesizer import Synthesizer
from crivo.worker.queue import RunQueue

VAGAS = [
    {
        "id": "li-1", "titulo": "SRE Manager", "empresa": "Fintech",
        "url": "https://exemplo.br/vaga/1", "local": "Sao Paulo",
        "modelo": "remote", "publicada_em": "2026-08-20", "score": 82,
        "componentes": {"competencias": 0.8}, "sinais": ["top_applicant"],
        "lacunas": ["observabilidade"], "diferenciais": ["kubernetes"],
        "blocker": None, "descricao": "Sobre a vaga: Kubernetes e Terraform",
        "html_bruto": "<div class='ruido'>lixo</div>",
    },
    {
        "id": "li-2", "titulo": "Head de Infra", "empresa": "Banco",
        "url": "https://exemplo.br/vaga/2", "local": "Recife",
        "modelo": "on-site", "publicada_em": "2026-08-19", "score": 61,
        "componentes": {}, "sinais": [], "lacunas": [], "diferenciais": [],
        "blocker": "presencial em Recife", "descricao": None,
    },
]

DESCARTADAS = [{"titulo": "Product Manager", "empresa": "X", "motivo": "titulo"}]
CONTEXTO = {"queries": ["SRE Manager"], "n_brutos": 40, "n_filtrados": 12}
PERFIL = {"headline": "Gerente de Infraestrutura", "nivel_inferido": "manager"}

RESPOSTA_OK = """Bloco 1 — Verdades desconfortaveis
[certo] O mercado para gerente de infraestrutura e raso.

Bloco 2 — Vagas
SRE Manager — Fintech
https://exemplo.br/vaga/1
Aderencia: 82%

Head de Infra — Banco
https://exemplo.br/vaga/2
Avaliacao inferida de titulo e empresa, sem leitura da descricao.
"""


def montar(limite_vagas=10, limite_descricao=8000):
    return prompt.build(
        perfil=PERFIL, vagas=VAGAS, descartadas=DESCARTADAS, contexto=CONTEXTO,
        limite_vagas=limite_vagas, limite_descricao=limite_descricao,
    )


# ------------------------------------------------------------------ payload
def test_only_the_closed_field_list_goes_up():
    corpo = montar().corpo
    assert "html_bruto" not in corpo
    assert "ruido" not in corpo


def test_the_payload_carries_score_gaps_and_blocker():
    corpo = montar().corpo
    assert "observabilidade" in corpo
    assert "presencial em Recife" in corpo
    assert '"score": 82' in corpo


def test_the_payload_is_limited_to_the_highest_scores():
    payload = montar(limite_vagas=1)
    assert payload.vagas == 1
    assert payload.ids_enviados == {"li-1"}


def test_descriptions_are_truncated_before_going_up():
    marcador = "ZQ"
    longa = dict(VAGAS[0], descricao=marcador * 5000)
    payload = prompt.build(
        perfil=PERFIL, vagas=[longa], descartadas=[], contexto=CONTEXTO,
        limite_vagas=10, limite_descricao=100,
    )
    # Marcador improvavel no restante do corpo, para medir so a descricao.
    assert payload.corpo.count(marcador) == 50


def test_a_job_without_a_description_is_flagged_as_such():
    corpo = montar().corpo
    assert '"descricao_disponivel": false' in corpo


def test_discarded_jobs_travel_with_their_reason():
    assert "Product Manager" in montar().corpo


def test_the_task_asks_for_classification_and_provenance():
    corpo = montar().corpo
    assert "provavel ou suposicao" in corpo
    assert "sem leitura da descricao" in corpo


# ------------------------------------------------------------- aterramento
def test_a_grounded_answer_is_accepted():
    assert grounding.accept(RESPOSTA_OK, montar(), True) == RESPOSTA_OK


def test_an_invented_url_is_refused():
    resposta = RESPOSTA_OK + "\nVeja tambem https://inventada.br/vaga/99"
    with pytest.raises(grounding.GroundingViolation) as err:
        grounding.accept(resposta, montar(), True)
    assert "inventada.br" in str(err.value)


def test_an_invented_job_identifier_is_refused():
    resposta = RESPOSTA_OK + "\nA vaga li-999 tambem serve."
    relatorio = grounding.verify(resposta, montar())
    assert relatorio.aterrada is False
    assert "li-999" in relatorio.ids_estranhos


def test_an_answer_without_classification_is_refused():
    resposta = "Bloco 2\nSRE Manager\nhttps://exemplo.br/vaga/1"
    relatorio = grounding.verify(resposta, montar())
    assert "classificacao das afirmacoes" in relatorio.faltando


def test_the_whole_answer_is_discarded_not_partially_used():
    resposta = RESPOSTA_OK + "\nhttps://inventada.br/x"
    relatorio = grounding.verify(resposta, montar())
    assert relatorio.aterrada is False


def test_a_missing_provenance_note_is_refused_when_a_job_lacks_description():
    sem_marca = RESPOSTA_OK.replace(
        "Avaliacao inferida de titulo e empresa, sem leitura da descricao.", ""
    )
    with pytest.raises(grounding.GroundingViolation) as err:
        grounding.accept(sem_marca, montar(), True)
    assert "vaga lida" in str(err.value)


def test_provenance_is_not_required_when_every_job_was_read():
    sem_marca = RESPOSTA_OK.replace(
        "Avaliacao inferida de titulo e empresa, sem leitura da descricao.", ""
    )
    assert grounding.accept(sem_marca, montar(), False) == sem_marca


def test_a_trailing_punctuation_does_not_make_a_url_foreign():
    resposta = RESPOSTA_OK.replace(
        "https://exemplo.br/vaga/1", "https://exemplo.br/vaga/1."
    )
    assert grounding.verify(resposta, montar()).aterrada is True


# ------------------------------------------------------------- sintetizador
class FakeRouter:
    def __init__(self, texto=RESPOSTA_OK, erro=None):
        self.texto = texto
        self.erro = erro
        self.chamadas = 0

    def complete(self, destinations, system, user):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return Completion(
            texto=self.texto, provedor="groq", modelo="llama",
            tokens_entrada=1200, tokens_saida=800,
        )


def aceita(provider, secret):
    return True


@pytest.fixture
def env(tmp_path):
    connection = open_database(tmp_path / "crivo.db")
    repo = Repository(connection)
    repo.insert(
        "users",
        {"user_id": "ana", "subject_google": "sub-ana", "criado_em": "2026-01-01"},
    )
    RunQueue(connection).enqueue("ana")
    run_id = connection.execute("SELECT run_id FROM runs").fetchone()[0]
    yield connection, run_id
    connection.close()


def synth(env, router=None, com_credencial=True, deterministico=False):
    connection, _run = env
    config = load_config()
    if deterministico:
        object.__setattr__(config.synthesis, "modo_deterministico", True)
    registry = ProviderRegistry(load_providers())
    vault = CredentialVault(connection, EnvelopeCipher(generate_master_key()), registry)
    if com_credencial:
        vault.store("ana", "groq", "chave", validate=aceita)
    client = ModelClient("ana", vault, registry, router or FakeRouter(), 8000)
    return Synthesizer(connection, client, config)


def executar(sintetizador, run_id, vagas=None):
    return sintetizador.synthesize(
        "ana", run_id, PERFIL, vagas if vagas is not None else VAGAS,
        DESCARTADAS, CONTEXTO,
    )


def test_one_logical_request_per_run(env):
    _c, run_id = env
    router = FakeRouter()
    executar(synth(env, router), run_id)
    assert router.chamadas == 1


def test_the_provenance_and_tokens_reach_the_run_record(env):
    connection, run_id = env
    executar(synth(env), run_id)
    linha = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert linha["provedor_sintese"] == "groq"
    assert (linha["tokens_entrada"], linha["tokens_saida"]) == (1200, 800)
    assert linha["falha_sintese"] is None


def test_an_exhausted_chain_records_a_synthesis_failure(env):
    connection, run_id = env
    router = FakeRouter(erro=DeterministicFallback("todos recusaram"))
    resultado = executar(synth(env, router), run_id)
    assert resultado.disponivel is False
    linha = connection.execute("SELECT falha_sintese FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert "cadeia esgotada" in linha[0]


def test_a_grounding_violation_records_a_failure_and_keeps_provenance(env):
    connection, run_id = env
    router = FakeRouter(texto=RESPOSTA_OK + "\nhttps://inventada.br/x")
    resultado = executar(synth(env, router), run_id)
    assert resultado.disponivel is False
    linha = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert "aterramento" in linha["falha_sintese"]
    assert linha["provedor_sintese"] == "groq"


def test_without_a_credential_no_request_is_emitted(env):
    _c, run_id = env
    router = FakeRouter()
    resultado = executar(synth(env, router, com_credencial=False), run_id)
    assert resultado.disponivel is False
    assert router.chamadas == 0


def test_deterministic_mode_never_calls_the_model(env):
    _c, run_id = env
    router = FakeRouter()
    resultado = executar(synth(env, router, deterministico=True), run_id)
    assert resultado.disponivel is False
    assert resultado.falha == "modo deterministico"
    assert router.chamadas == 0


def test_a_run_without_jobs_does_not_call_the_model(env):
    _c, run_id = env
    router = FakeRouter()
    resultado = executar(synth(env, router), run_id, vagas=[])
    assert router.chamadas == 0
    assert "nenhuma vaga" in resultado.falha
