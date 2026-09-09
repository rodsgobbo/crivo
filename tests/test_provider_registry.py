from pathlib import Path

import pytest

from crivo.providers.registry import (
    ProviderConfigError,
    ProviderRegistry,
    load_providers,
)

BASE = Path("config") / "providers.toml"

ENTRADA_VALIDA = """
[[provedor]]
id = "{id}"
rotulo = "Rotulo"
endereco = "https://exemplo"
formato_credencial = "{formato}"
execucao = "{execucao}"
modelos = ["m1"]
limite_declarado = "cota gratuita"
destino_dos_dados = "servidores do provedor"
"""


def write(tmp_path, body):
    target = tmp_path / "providers.toml"
    target.write_text(body, encoding="utf-8")
    return target


def test_the_shipped_registry_loads():
    provedores = load_providers(BASE)
    assert {p.id for p in provedores} >= {"google-ai-studio", "ollama-local"}


def test_a_local_provider_needs_no_credential():
    local = next(p for p in load_providers(BASE) if p.id == "ollama-local")
    assert local.exige_credencial is False
    assert local.roda_localmente is True


def test_a_remote_provider_requires_a_static_key():
    remoto = next(p for p in load_providers(BASE) if p.id == "groq")
    assert remoto.exige_credencial is True
    assert remoto.roda_localmente is False


def test_a_provider_needing_an_application_is_refused(tmp_path):
    body = ENTRADA_VALIDA.format(
        id="exige-app", formato="oauth_aplicacao", execucao="rede_externa"
    )
    with pytest.raises(ProviderConfigError) as err:
        load_providers(write(tmp_path, body))
    assert "oauth_aplicacao" in str(err.value)
    assert "registro de aplicacao" in str(err.value)


def test_a_missing_field_names_it(tmp_path):
    body = ENTRADA_VALIDA.format(
        id="x", formato="chave_estatica", execucao="rede_externa"
    ).replace('limite_declarado = "cota gratuita"\n', "")
    with pytest.raises(ProviderConfigError) as err:
        load_providers(write(tmp_path, body))
    assert "limite_declarado" in str(err.value)


def test_an_unknown_execution_place_is_refused(tmp_path):
    body = ENTRADA_VALIDA.format(
        id="x", formato="chave_estatica", execucao="na_nuvem_magica"
    )
    with pytest.raises(ProviderConfigError) as err:
        load_providers(write(tmp_path, body))
    assert "na_nuvem_magica" in str(err.value)


def test_repeated_identifiers_are_refused(tmp_path):
    body = ENTRADA_VALIDA.format(
        id="igual", formato="chave_estatica", execucao="rede_externa"
    ) + ENTRADA_VALIDA.format(
        id="igual", formato="chave_estatica", execucao="rede_externa"
    )
    with pytest.raises(ProviderConfigError) as err:
        load_providers(write(tmp_path, body))
    assert "igual" in str(err.value)


def test_an_empty_registry_is_refused(tmp_path):
    with pytest.raises(ProviderConfigError):
        load_providers(write(tmp_path, "# vazio\n"))


def test_a_missing_registry_names_the_path(tmp_path):
    with pytest.raises(ProviderConfigError) as err:
        load_providers(tmp_path / "ausente.toml")
    assert "ausente.toml" in str(err.value)


# ------------------------------------------------------------ disponibilidade
def test_a_provider_that_does_not_answer_leaves_the_offer():
    registry = ProviderRegistry(load_providers(BASE))
    registry.check_availability(lambda p: p.id != "groq")
    oferecidos = {o.id for o in registry.offers()}
    assert "groq" not in oferecidos
    assert "google-ai-studio" in oferecidos


def test_the_reason_for_unavailability_is_recorded():
    registry = ProviderRegistry(load_providers(BASE))

    def probe(provider):
        if provider.id == "mistral":
            raise TimeoutError("sem resposta")
        return True

    indisponiveis = registry.check_availability(probe)
    assert "TimeoutError" in indisponiveis["mistral"]
    assert registry.unavailable == indisponiveis


def test_a_new_check_replaces_the_previous_result():
    registry = ProviderRegistry(load_providers(BASE))
    registry.check_availability(lambda p: False)
    assert registry.offers() == []
    registry.check_availability(lambda p: True)
    assert len(registry.offers()) == len(registry.all)


def test_the_offer_tells_the_user_the_limit_and_the_destination():
    registry = ProviderRegistry(load_providers(BASE))
    registry.check_availability(lambda p: True)
    oferta = next(o for o in registry.offers() if o.id == "ollama-local")
    assert oferta.limite_declarado
    assert "maquina do operador" in oferta.destino_dos_dados
    assert oferta.exige_credencial is False


def test_an_unknown_provider_resolves_to_nothing():
    assert ProviderRegistry(load_providers(BASE)).get("inventado") is None
