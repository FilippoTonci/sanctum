from sanctum.core.review.previews import effective_params


def test_user_added_type_token_becomes_redacted_marker() -> None:
    assert effective_params("USER_ADDED", "replace", {}) == {"new_value": "[REDACTED]"}


def test_explicit_new_value_wins() -> None:
    assert effective_params("USER_ADDED", "replace", {"new_value": "X"}) == {"new_value": "X"}


def test_other_entities_are_untouched() -> None:
    assert effective_params("PERSON", "replace", {}) == {}


def test_other_operators_are_untouched() -> None:
    assert effective_params("USER_ADDED", "redact", None) == {}
