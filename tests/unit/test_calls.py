"""Read versus write classification of tool calls (SPEC 5.4b, M3 step 6)."""

import pytest

from pruefstand.graders.calls import classify_call, split_statements, sql_reads, sql_texts


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "  select 1;",
        "-- check\nSELECT 1",
        "/* why */ WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SELECT * FROM t",
        "SHOW search_path",
        "(SELECT 1) UNION (SELECT 2)",
        "SELECT 'DELETE FROM t' AS text",  # a keyword inside a string does not count
        "SELECT 1; SELECT 2;",
        "",
    ],
)
def test_read_sql(sql):
    assert sql_reads(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE t SET a = 1",
        "CREATE INDEX i ON t (a)",
        "SELECT 1; DROP TABLE t",  # any write in several statements makes it a write
        "WITH gone AS (DELETE FROM t RETURNING *) SELECT * FROM gone",
        "SELECT * INTO copy FROM t",
        "EXPLAIN ANALYZE DELETE FROM t",
        "-- SELECT\nINSERT INTO t VALUES (1)",  # the comment does not make it a read
        "BEGIN",
    ],
)
def test_write_sql(sql):
    assert not sql_reads(sql)


def test_split_statements_respects_quotes_and_dollar_quotes():
    sql = "SELECT 'a;b'; CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql;"
    assert split_statements(sql) == [
        "SELECT 'a;b'",
        "CREATE FUNCTION f() RETURNS int AS $$ SELECT 1; $$ LANGUAGE sql",
    ]


def test_sql_texts_from_arguments():
    assert sql_texts({"sql": "SELECT 1"}) == ["SELECT 1"]
    assert sql_texts({"queries": ["SELECT 1", "SELECT 2"], "method": "dta"}) == [
        "SELECT 1",
        "SELECT 2",
    ]
    assert sql_texts({"path": "/x"}) == []


@pytest.mark.parametrize(
    ("name", "arguments", "kind", "rule"),
    [
        # postgres-mcp: SQL tools by their statements.
        ("execute_sql", {"sql": "SELECT count(*) FROM payment"}, "read", "sql"),
        ("execute_sql", {"sql": "CREATE INDEX ON payment (customer_id)"}, "write", "sql"),
        ("explain_query", {"sql": "SELECT 1", "analyze": False}, "read", "sql"),
        ("analyze_query_indexes", {"queries": ["SELECT 1"]}, "read", "sql"),
        # Other tools by name.
        ("list_schemas", {}, "read", "name_read"),
        ("get_object_details", {"schema_name": "public"}, "read", "name_read"),
        ("read_text_file", {"path": "/w/a.txt"}, "read", "name_read"),
        ("directory_tree", {"path": "/w"}, "read", "name_read"),
        ("write_file", {"path": "/w/a.txt", "content": "SELECT 1"}, "write", "name_write"),
        ("move_file", {"source": "a", "destination": "b"}, "write", "name_write"),
        ("create_directory", {"path": "/w/x"}, "write", "name_write"),
        # Unknown names count as writes.
        ("analyze_db_health", {}, "write", "unknown"),
        ("compliance_audit", {"data": "x"}, "write", "unknown"),
    ],
)
def test_classify_call(name, arguments, kind, rule):
    result = classify_call(name, arguments)
    assert (result.kind, result.rule) == (kind, rule)
