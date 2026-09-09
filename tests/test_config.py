from pathlib import Path

import pytest

from crivo.config import ConfigError, read_config_file


def test_reads_every_configured_section_from_the_default_file():
    raw = read_config_file(Path("config") / "default.toml")
    assert set(raw) >= {
        "collection", "run", "session", "retention", "resume",
        "synthesis", "report", "profile", "scoring", "providers",
    }
    # O valor e fixado aqui para provar que o arquivo foi lido de verdade, e
    # nao que 400 seja o numero certo -- essa decisao mora no proprio TOML,
    # junto com o historico de medicoes que a sustenta.
    assert raw["collection"]["orcamento_diario_coleta"] == 400
    assert raw["profile"]["precedencia_origens"] == ["manual", "resume", "linkedin"]


def test_missing_file_names_the_path(tmp_path):
    missing = tmp_path / "ausente.toml"
    with pytest.raises(ConfigError) as err:
        read_config_file(missing)
    assert str(missing) in str(err.value)


def test_unreadable_file_names_the_path(tmp_path):
    broken = tmp_path / "quebrado.toml"
    broken.write_text("isto = nao [ e toml", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        read_config_file(broken)
    assert str(broken) in str(err.value)


# --------------------------------------------------------- arquivo de ambiente
def test_the_env_file_reaches_the_environment(tmp_path):
    """O README manda criar `.env`, e por um tempo nada o lia.

    Todo criterio sobre segredo estava satisfeito -- eles vem do ambiente -- e
    nenhum dizia como chegam la. O produto nao subia com o arquivo no lugar.
    """
    from crivo.secrets_vault import load_env_file

    arquivo = tmp_path / ".env"
    arquivo.write_text("CRIVO_MASTER_KEY=abc123\n", encoding="utf-8")
    ambiente: dict[str, str] = {}
    assert load_env_file(arquivo, ambiente) == ["CRIVO_MASTER_KEY"]
    assert ambiente["CRIVO_MASTER_KEY"] == "abc123"


def test_a_real_environment_variable_wins_over_the_file(tmp_path):
    """Em producao os valores vem do orquestrador; um `.env` esquecido no disco
    nao pode sobrescrever o que foi injetado deliberadamente."""
    from crivo.secrets_vault import load_env_file

    arquivo = tmp_path / ".env"
    arquivo.write_text("CRIVO_MASTER_KEY=do-arquivo\n", encoding="utf-8")
    ambiente = {"CRIVO_MASTER_KEY": "do-ambiente"}
    assert load_env_file(arquivo, ambiente) == []
    assert ambiente["CRIVO_MASTER_KEY"] == "do-ambiente"


def test_comments_blank_lines_and_export_prefixes_are_handled(tmp_path):
    from crivo.secrets_vault import load_env_file

    arquivo = tmp_path / ".env"
    arquivo.write_text(
        "# comentario\n\nexport GOOGLE_CLIENT_ID=cliente\nsem_igual\n",
        encoding="utf-8",
    )
    ambiente: dict[str, str] = {}
    assert load_env_file(arquivo, ambiente) == ["GOOGLE_CLIENT_ID"]
    assert ambiente["GOOGLE_CLIENT_ID"] == "cliente"


def test_quotes_delimit_the_value_and_do_not_become_part_of_it(tmp_path):
    """Uma chave entre aspas entraria no ambiente com elas e falharia na
    decodificacao muito longe daqui."""
    from crivo.secrets_vault import load_env_file

    arquivo = tmp_path / ".env"
    arquivo.write_text('CRIVO_MASTER_KEY="com aspas"\n', encoding="utf-8")
    ambiente: dict[str, str] = {}
    load_env_file(arquivo, ambiente)
    assert ambiente["CRIVO_MASTER_KEY"] == "com aspas"


def test_an_absent_env_file_is_not_an_error(tmp_path):
    """Quem exporta as variaveis na mao nao precisa do arquivo."""
    from crivo.secrets_vault import load_env_file

    assert load_env_file(tmp_path / "nao-existe", {}) == []
