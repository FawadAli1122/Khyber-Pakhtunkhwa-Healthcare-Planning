import pytest

from server.telegram_admin_db import parse_field_updates


def test_parse_field_updates_parses_multiple_lines():
    result = parse_field_updates("name=Fridge A\ncapacity=50")
    assert result == {"name": "Fridge A", "capacity": "50"}


def test_parse_field_updates_value_may_contain_equals_sign():
    result = parse_field_updates("formula=a=b+c")
    assert result == {"formula": "a=b+c"}


def test_parse_field_updates_value_may_contain_comma():
    result = parse_field_updates("narrative=Peshawar, Mardan, and Charsadda")
    assert result == {"narrative": "Peshawar, Mardan, and Charsadda"}


def test_parse_field_updates_ignores_blank_lines():
    result = parse_field_updates("name=Fridge A\n\n\ncapacity=50\n")
    assert result == {"name": "Fridge A", "capacity": "50"}


def test_parse_field_updates_rejects_line_without_equals():
    with pytest.raises(ValueError, match="="):
        parse_field_updates("name=Fridge A\njust some text")


def test_parse_field_updates_rejects_empty_column_name():
    with pytest.raises(ValueError):
        parse_field_updates("=some value")


def test_parse_field_updates_rejects_empty_input():
    with pytest.raises(ValueError):
        parse_field_updates("")


def test_parse_field_updates_rejects_only_blank_lines():
    with pytest.raises(ValueError):
        parse_field_updates("\n\n  \n")
