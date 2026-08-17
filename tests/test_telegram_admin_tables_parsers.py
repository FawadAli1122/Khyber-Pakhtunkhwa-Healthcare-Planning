import pytest

from server.telegram_admin_tables import parse_column_spec, parse_pipe_row


def test_parse_column_spec_parses_valid_input():
    result = parse_column_spec("name:text, capacity:number, opened:date")
    assert result == [
        {"label": "name", "type": "text"},
        {"label": "capacity", "type": "number"},
        {"label": "opened", "type": "date"},
    ]


def test_parse_column_spec_strips_whitespace():
    result = parse_column_spec("  name : text ,  capacity : number  ")
    assert result == [{"label": "name", "type": "text"}, {"label": "capacity", "type": "number"}]


def test_parse_column_spec_rejects_empty_input():
    with pytest.raises(ValueError):
        parse_column_spec("")


def test_parse_column_spec_rejects_missing_colon():
    with pytest.raises(ValueError, match="name:type"):
        parse_column_spec("name")


def test_parse_column_spec_rejects_unknown_type():
    with pytest.raises(ValueError, match="text, number, date"):
        parse_column_spec("name:integer")


def test_parse_column_spec_rejects_empty_label():
    with pytest.raises(ValueError):
        parse_column_spec(":text")


def test_parse_pipe_row_maps_positionally():
    columns = [{"column_name": "name"}, {"column_name": "capacity"}]
    result = parse_pipe_row("Peshawar DHQ | 50", columns)
    assert result == {"name": "Peshawar DHQ", "capacity": "50"}


def test_parse_pipe_row_strips_whitespace():
    columns = [{"column_name": "name"}]
    result = parse_pipe_row("  Peshawar DHQ  ", columns)
    assert result == {"name": "Peshawar DHQ"}


def test_parse_pipe_row_rejects_count_mismatch():
    columns = [{"column_name": "name"}, {"column_name": "capacity"}]
    with pytest.raises(ValueError, match="2 value"):
        parse_pipe_row("Peshawar DHQ", columns)
