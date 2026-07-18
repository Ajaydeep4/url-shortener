import pytest

from shortener_service.exceptions import InvalidAliasError
from shortener_service.service import validate_custom_alias


@pytest.mark.parametrize("alias", ["abc", "my-link", "My_Link42", "a" * 32, "A-1_b"])
def test_valid_aliases_pass(alias):
    validate_custom_alias(alias)


@pytest.mark.parametrize(
    "alias",
    [
        "ab",  # too short
        "a" * 33,  # too long
        "has space",
        "has/slash",
        "emoji😀",
        "dot.dot",
        "",
    ],
)
def test_invalid_format_rejected(alias):
    with pytest.raises(InvalidAliasError):
        validate_custom_alias(alias)


@pytest.mark.parametrize("alias", ["api", "API", "docs", "healthz", "metrics"])
def test_reserved_words_rejected(alias):
    with pytest.raises(InvalidAliasError):
        validate_custom_alias(alias)
