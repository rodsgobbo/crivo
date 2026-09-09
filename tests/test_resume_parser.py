import json

import pytest

from crivo.config import load_config
from crivo.providers.client import Completion, DeterministicFallback, ModelClient
from crivo.providers.registry import ProviderRegistry, load_providers
from crivo.providers.vault import (
    CredentialVault,
    EnvelopeCipher,
    generate_master_key,
)
from crivo.resume.importer import ResumeImporter
from crivo.resume.parser import (
    CAMPOS,
    ExtractionError,
    ManualEntryRequired,
    ResumeParser,
)
from crivo.store.migrations import open_database
from crivo.store.repository import Repository

TEXTO = (
    "Ana Ribeiro. Gerente de Infraestrutura e Cloud. Campinas, SP. "
    "Liderou times de SRE em fintech com Kubernetes, Terraform e observabilidade. "
) * 4

RESPOSTA_BOA = json.dumps(
    {
        "nome": "Ana Ribeiro",
        "headline": "Gerente de Infraestrutura e Cloud",
        "localizacao": "Campinas, SP",
        "experiencias": [
            {"titulo": "Gerente de Infraestrutura", "empresa": "Fintech",
             "inicio": "2020-01", "fim": None, "descricao": "SRE e plataforma"}
        ],
        "competencias": ["Kubernetes", "Terraform"],
        "formacao": None,
        "idiomas": None,
        "trechos_origem": {"nome": "Ana Ribeiro", "inventado": "x"},
    },
    ensure_ascii=False,
)


class FakeDrive:
    scope = "https://www.googleapis.com/auth/drive.file"

    def export_text(self, file_id, access_token):
        return TEXTO


class FakeRouter:
    def __init__(self, texto=RESPOSTA_BOA, erro=None):
        self.texto = texto
        self.erro = erro
        self.chamadas = 0

    def complete(self, destinations, system, user):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return Completion(
            texto=self.texto, provedor=destinations[0].provider_id,
            modelo=destinations[0].modelo, tokens_entrada=100, tokens_saida=50,
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
    config = load_config()
    registry = ProviderRegistry(load_providers())
    vault = CredentialVault(connection, EnvelopeCipher(generate_master_key()), registry)
    importer = ResumeImporter(connection, config, FakeDrive())
    resume = importer.import_from_drive("ana", "f", "t")
    yield connection, config, vault, registry, resume
    connection.close()


def parser(env, router=None, com_credencial=True):
    connection, config, vault, registry, _resume = env
    if com_credencial:
        vault.store("ana", "groq", "chave", validate=aceita)
    client = ModelClient(
        "ana", vault, registry, router or FakeRouter(),
        config.synthesis.limite_caracteres_texto_externo,
    )
    return ResumeParser(connection, client)


# ------------------------------------------------------------------ extracao
def test_extraction_fills_the_closed_field_list(env):
    _c, _cfg, _v, _r, resume = env
    extraida = parser(env).extract("ana", resume.resume_id)
    assert set(extraida.campos) == set(CAMPOS)
    assert extraida.campos["nome"] == "Ana Ribeiro"


def test_fields_outside_the_closed_list_are_discarded(env):
    _c, _cfg, _v, _r, resume = env
    extraida = parser(env).extract("ana", resume.resume_id)
    assert "inventado" not in extraida.trechos_origem
    assert extraida.trechos_origem["nome"] == "Ana Ribeiro"


def test_absent_fields_become_gaps(env):
    _c, _cfg, _v, _r, resume = env
    extraida = parser(env).extract("ana", resume.resume_id)
    assert set(extraida.lacunas) == {"formacao", "idiomas"}


def test_the_provenance_is_recorded(env):
    _c, _cfg, _v, _r, resume = env
    extraida = parser(env).extract("ana", resume.resume_id)
    assert extraida.provedor == "groq"
    assert extraida.modelo


def test_a_response_wrapped_in_a_code_fence_is_read(env):
    _c, _cfg, _v, _r, resume = env
    router = FakeRouter(texto=f"```json\n{RESPOSTA_BOA}\n```")
    assert parser(env, router).extract("ana", resume.resume_id).campos["nome"]


def test_a_response_without_json_is_refused(env):
    _c, _cfg, _v, _r, resume = env
    router = FakeRouter(texto="nao vou responder em JSON")
    with pytest.raises(ExtractionError) as err:
        parser(env, router).extract("ana", resume.resume_id)
    assert "JSON" in str(err.value)


def test_a_broken_json_is_refused(env):
    _c, _cfg, _v, _r, resume = env
    with pytest.raises(ExtractionError):
        parser(env, FakeRouter(texto='{"nome": ')).extract("ana", resume.resume_id)


def test_an_unknown_resume_is_refused(env):
    with pytest.raises(ExtractionError):
        parser(env).extract("ana", "resume-inventado")


# ------------------------------------------------------------------ reuso
def test_identical_content_reuses_the_extraction(env):
    connection, config, vault, _r, resume = env
    router = FakeRouter()
    p = parser(env, router)
    p.extract("ana", resume.resume_id)
    outro = ResumeImporter(connection, config, FakeDrive()).import_from_drive(
        "ana", "f2", "t"
    )
    p.extract("ana", outro.resume_id)
    assert router.chamadas == 1


def test_the_reused_extraction_keeps_the_original_provenance(env):
    connection, config, _v, _r, resume = env
    p = parser(env)
    primeira = p.extract("ana", resume.resume_id)
    outro = ResumeImporter(connection, config, FakeDrive()).import_from_drive(
        "ana", "f2", "t"
    )
    segunda = p.extract("ana", outro.resume_id)
    assert segunda.provedor == primeira.provedor
    assert segunda.campos == primeira.campos


# ------------------------------------------------------------------ portao
def test_an_extraction_is_born_unconfirmed(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    assert extraida.confirmada is False
    assert p.confirmed("ana") is None
    assert [e.extraction_id for e in p.pending("ana")] == [extraida.extraction_id]


def test_confirming_opens_the_gate(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    confirmada = p.confirm("ana", extraida.extraction_id)
    assert confirmada.confirmada is True
    assert p.confirmed("ana").extraction_id == extraida.extraction_id
    assert p.pending("ana") == []


def test_confirming_twice_is_refused(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    p.confirm("ana", extraida.extraction_id)
    with pytest.raises(ExtractionError):
        p.confirm("ana", extraida.extraction_id)


def test_confirming_an_unknown_extraction_is_refused(env):
    with pytest.raises(ExtractionError):
        parser(env).confirm("ana", "inventada")


# ------------------------------------------------------------------ correcao
def test_a_correction_does_not_overwrite_the_extracted_value(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    corrigida = p.correct("ana", extraida.extraction_id, "headline", "Head de SRE")
    assert corrigida.campos["headline"] == "Gerente de Infraestrutura e Cloud"
    assert corrigida.correcoes["headline"] == "Head de SRE"
    assert corrigida.efetivo()["headline"] == "Head de SRE"


def test_corrections_accumulate(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    p.correct("ana", extraida.extraction_id, "headline", "A")
    corrigida = p.correct("ana", extraida.extraction_id, "localizacao", "Sao Paulo, SP")
    assert set(corrigida.correcoes) == {"headline", "localizacao"}


def test_correcting_an_unknown_field_is_refused(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env)
    extraida = p.extract("ana", resume.resume_id)
    with pytest.raises(ExtractionError) as err:
        p.correct("ana", extraida.extraction_id, "signo", "aquario")
    assert "signo" in str(err.value)


# ------------------------------------------------------- caminhos alternativos
def test_without_a_credential_manual_entry_is_offered(env):
    _c, _cfg, _v, _r, resume = env
    with pytest.raises(ManualEntryRequired) as err:
        parser(env, com_credencial=False).extract("ana", resume.resume_id)
    assert "manualmente" in str(err.value)


def test_without_a_credential_no_request_is_emitted(env):
    _c, _cfg, _v, _r, resume = env
    router = FakeRouter()
    with pytest.raises(ManualEntryRequired):
        parser(env, router, com_credencial=False).extract("ana", resume.resume_id)
    assert router.chamadas == 0


def test_an_exhausted_chain_offers_manual_entry(env):
    _c, _cfg, _v, _r, resume = env
    router = FakeRouter(erro=DeterministicFallback("todos recusaram"))
    with pytest.raises(ManualEntryRequired):
        parser(env, router).extract("ana", resume.resume_id)


EXPERIENCIA = [
    {
        "titulo": "SRE",
        "empresa": "Acme",
        "inicio": "2019-03",
        "fim": None,
        "descricao": "Python",
    }
]


def test_manual_entry_produces_a_confirmed_extraction(env):
    _c, _cfg, _v, _r, resume = env
    p = parser(env, com_credencial=False)
    extraida = p.manual(
        "ana", resume.resume_id,
        {"nome": "Ana", "headline": "SRE", "experiencias": EXPERIENCIA},
    )
    assert extraida.confirmada is True
    assert extraida.provedor is None
    assert p.confirmed("ana").campos["nome"] == "Ana"


def test_manual_entry_without_experience_is_refused(env):
    """Confirmar em branco travava o fluxo tres passos adiante.

    O ato de preencher a mao ja confirma a extracao, entao um formulario vazio
    produzia um perfil sem historico e uma home que nao oferecia mais extrair.
    O run recusava depois, longe da causa. A exigencia passa a ser cobrada aqui,
    com o formulario ainda aberto.
    """
    _c, _cfg, _v, _r, resume = env
    p = parser(env, com_credencial=False)
    with pytest.raises(ExtractionError) as err:
        p.manual("ana", resume.resume_id, {"nome": "Ana"})
    assert "experiencias" in str(err.value)
    assert p.confirmed("ana") is None, "nada pode ter sido gravado"


def test_the_resume_text_is_wrapped_as_untrusted_data(env):
    """O texto do curriculo sobe delimitado, como qualquer origem externa."""
    from crivo.providers.client import ABERTURA, FECHAMENTO

    _c, _cfg, _v, _r, resume = env
    capturado = {}

    class RoteadorQueCaptura(FakeRouter):
        def complete(self, destinations, system, user):
            capturado["system"] = system
            capturado["user"] = user
            return super().complete(destinations, system, user)

    parser(env, RoteadorQueCaptura()).extract("ana", resume.resume_id)

    assert ABERTURA in capturado["user"]
    assert FECHAMENTO in capturado["user"]
    # O texto do curriculo nao alcanca a instrucao de sistema.
    assert "Ana Ribeiro" not in capturado["system"]
