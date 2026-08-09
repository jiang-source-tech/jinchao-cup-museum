import pytest

from core.providers.llm.openai.credentials import resolve_api_key, resolve_max_retries


def test_api_key_can_be_loaded_from_environment_without_yaml_secret():
    assert (
        resolve_api_key(
            {"api_key_env": "DEEPSEEK_API_KEY"},
            {"DEEPSEEK_API_KEY": "secret-from-environment"},
        )
        == "secret-from-environment"
    )


def test_api_key_environment_variable_is_fail_closed_when_missing():
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY is not set"):
        resolve_api_key(
            {
                "api_key_env": "DEEPSEEK_API_KEY",
                "api_key": "must-not-be-used-as-a-fallback",
            },
            {},
        )


def test_existing_inline_api_key_configuration_remains_supported():
    assert resolve_api_key({"api_key": "legacy-secret"}, {}) == "legacy-secret"


@pytest.mark.parametrize("value", [-1, 11, "not-an-integer"])
def test_invalid_max_retries_is_rejected(value):
    with pytest.raises(ValueError, match="max_retries"):
        resolve_max_retries({"max_retries": value})
