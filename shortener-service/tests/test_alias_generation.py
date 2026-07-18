from shortener_service.service import ALIAS_ALPHABET, generate_alias


def test_generated_alias_has_requested_length():
    for length in (4, 7, 12):
        assert len(generate_alias(length)) == length


def test_generated_alias_uses_base62_charset_only():
    for _ in range(200):
        alias = generate_alias(7)
        assert all(c in ALIAS_ALPHABET for c in alias)


def test_generated_aliases_are_random():
    aliases = {generate_alias(7) for _ in range(1000)}
    # With 62^7 possibilities, 1000 draws colliding would indicate broken randomness.
    assert len(aliases) == 1000
