from backend.core.search_query import build_fts_match_query, parse_search_query


def test_empty_query():
    parsed = parse_search_query("")
    assert parsed.field_filters == {}
    assert parsed.free_text_terms == []


def test_none_query():
    parsed = parse_search_query(None)
    assert parsed.field_filters == {}
    assert parsed.free_text_terms == []


def test_bare_free_text():
    parsed = parse_search_query("timeout")
    assert parsed.field_filters == {}
    assert parsed.free_text_terms == ["timeout"]


def test_field_value_recognized():
    parsed = parse_search_query("level:ERROR")
    assert parsed.field_filters == {"level": "ERROR"}
    assert parsed.free_text_terms == []


def test_level_value_uppercased():
    parsed = parse_search_query("level:error")
    assert parsed.field_filters == {"level": "ERROR"}


def test_source_alias_maps_to_source_system():
    parsed = parse_search_query("source:cardinal")
    assert parsed.field_filters == {"source_system": "cardinal"}


def test_source_system_alias_also_works():
    parsed = parse_search_query("source_system:cardinal")
    assert parsed.field_filters == {"source_system": "cardinal"}


def test_component_field():
    parsed = parse_search_query("component:auth")
    assert parsed.field_filters == {"component": "auth"}


def test_unrecognized_field_treated_as_free_text():
    parsed = parse_search_query("merchant:acme")
    assert parsed.field_filters == {}
    assert parsed.free_text_terms == ["merchant:acme"]


def test_quoted_phrase_unquoted_as_one_term():
    parsed = parse_search_query('"exact phrase"')
    assert parsed.free_text_terms == ["exact phrase"]


def test_field_and_free_text_combine():
    parsed = parse_search_query('level:ERROR source:cardinal "exact phrase" timeout')
    assert parsed.field_filters == {"level": "ERROR", "source_system": "cardinal"}
    assert parsed.free_text_terms == ["exact phrase", "timeout"]


def test_unbalanced_quotes_falls_back_to_whole_string():
    parsed = parse_search_query('"unterminated')
    assert parsed.field_filters == {}
    assert parsed.free_text_terms == ['"unterminated']


def test_build_fts_match_query_empty():
    assert build_fts_match_query([]) is None


def test_build_fts_match_query_single_term():
    assert build_fts_match_query(["timeout"]) == '"timeout"'


def test_build_fts_match_query_ands_multiple_terms():
    assert build_fts_match_query(["timeout", "exact phrase"]) == '"timeout" AND "exact phrase"'


def test_build_fts_match_query_escapes_embedded_quotes():
    assert build_fts_match_query(['say "hi"']) == '"say ""hi"""'
