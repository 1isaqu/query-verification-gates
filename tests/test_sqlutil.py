from __future__ import annotations

import pytest

from query_gates.sqlutil import (
    ReadOnlyViolation,
    assert_read_only,
    canonical,
    cte_names,
    extract_refs,
    extract_tables,
    iter_table_refs,
    strip_comments,
    strip_noise,
)


class TestStripping:
    def test_comments_removed_literals_kept(self):
        sql = "SELECT 'a -- not a comment' -- real comment\n, 1 /* block */ FROM t"
        out = strip_comments(sql)
        assert "'a -- not a comment'" in out
        assert "real comment" not in out
        assert "block" not in out

    def test_quote_inside_comment_does_not_open_a_literal(self):
        out = strip_noise("SELECT 1 -- it's fine\nFROM t WHERE x = 'y'")
        assert "FROM t" in out
        assert "it's" not in out

    def test_noise_blanks_literals_and_quoted_identifiers(self):
        out = strip_noise("SELECT [delete], 'DROP TABLE x' FROM t")
        assert "DROP" not in out
        assert "delete" not in out

    def test_unquote_keeps_identifier_text(self):
        out = strip_noise('SELECT * FROM [dbo].[order lines] x', unquote_identifiers=True)
        assert "dbo.order_lines" in out

    def test_doubled_quote_stays_inside_literal(self):
        out = strip_noise("SELECT 'it''s DROP' FROM t")
        assert "DROP" not in out

    def test_doubled_closing_bracket_stays_inside_the_identifier(self):
        out = strip_noise("SELECT 1 AS [a]]'] UPDATE t SET x = 1 SELECT 'z'")
        assert "UPDATE" in out
        assert "'" not in out.replace("''", "")  # the literal is 'z', blanked to ''
        assert strip_noise("SELECT [a]]--b] FROM t") == "SELECT _q_ FROM t"

    def test_doubled_double_quote_stays_inside_the_identifier(self):
        assert strip_noise('SELECT "a""b" FROM t') == "SELECT _q_ FROM t"

    def test_canonical_ignores_whitespace_case_and_comments_but_not_literals(self):
        assert canonical("SELECT  1 -- c\nFROM T") == canonical("select 1 from t")
        assert canonical("SELECT 'A'") != canonical("SELECT 'B'")


class TestReadOnlyGuard:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "  select * from t where created_at > '2026-01-01';",
            "WITH x AS (SELECT 1 AS n) SELECT n FROM x",
            "(SELECT 1) UNION ALL (SELECT 2)",
            "SELECT delete_flag, last_update, update_count FROM t",
            "SELECT 'DROP TABLE t; DELETE FROM t' AS note",
            "SELECT 1 -- DROP TABLE t",
            "SELECT 1 /* INSERT INTO t VALUES (1) */",
            "SELECT [update] FROM t",
            "VALUES (1), (2)",
            "SELECT backup_id, restore_date, use_count, kill_switch, shutdown_at FROM t",
            "SELECT [use], [backup] FROM t",
            "SELECT 'OPENQUERY(lnk, 1)', 'BEGIN TRAN' FROM t",
            "SELECT begin_at, transaction_id FROM t",
            "SELECT [a]]b], [UPDATE]]] FROM t",
            'SELECT "a""UPDATE" FROM t',
            "SELECT 'a /* b' AS x, 'c */ d' AS y FROM t",
            "SELECT /*+ index(t ix) */ 1 FROM t /* plain comment */",
            "SELECT receive_date, send_on, end_conversation_id, waitfor_ms, next_value FROM t",
            "SELECT [checkpoint], [receive], [waitfor] FROM t",
        ],
    )
    def test_accepts_reads(self, sql):
        assert_read_only(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1 UPDATETEXT t.col @ptr 0 0 'x'",
            "SELECT 1 WRITETEXT t.col @ptr 'x'",
            "SELECT * FROM OPENQUERY(lnk, 'UPDATE t SET x = 1')",
            "SELECT * FROM OPENROWSET('provider', 'conn', 'SELECT 1')",
            "SELECT * FROM OPENDATASOURCE('provider', 'conn').db.dbo.t",
            "SELECT 1 BACKUP DATABASE d TO DISK = 'f'",
            "SELECT 1 RESTORE DATABASE d FROM DISK = 'f'",
            "SELECT 1 SHUTDOWN WITH NOWAIT",
            "SELECT 1 KILL 55",
            "SELECT 1 DBCC CHECKDB",
            "SELECT 1 DENY SELECT ON t TO u",
            "SELECT 1 RECONFIGURE",
            "SELECT 1 USE other_db",
            "SELECT 1 BEGIN TRAN",
            "SELECT 1 begin   transaction",
            "SELECT 1 BEGIN/* c */DISTRIBUTED TRAN",
            "WITH x AS (SELECT 1 AS n) SELECT n FROM x BACKUP LOG d TO DISK = 'f'",
            "SELECT 1 DISABLE TRIGGER trg ON t",
            "SELECT 1 ENABLE TRIGGER ALL ON DATABASE",
            "SELECT 1 disable\n  trigger trg ON t",
            "SELECT 1 RECEIVE TOP (1) * FROM q",
            "SELECT 1 SEND ON CONVERSATION @h MESSAGE TYPE m",
            "SELECT 1 END CONVERSATION @h",
            "SELECT 1 MOVE CONVERSATION @c TO @g",
            "SELECT 1 GET CONVERSATION GROUP @g FROM q",
            "SELECT 1 BEGIN DIALOG @h FROM SERVICE a TO SERVICE 'b'",
            "SELECT 1 BEGIN CONVERSATION TIMER (@h) TIMEOUT = 5",
            "SELECT 1 CHECKPOINT",
            "SELECT 1 WAITFOR DELAY '00:00:05'",
            "SELECT 1 SETUSER 'x'",
            "SELECT 1 ADD SENSITIVITY CLASSIFICATION TO t.c WITH (LABEL = 'x')",
            "SELECT NEXT VALUE FOR seq_a",
        ],
    )
    def test_rejects_statements_chained_without_a_separator(self, sql):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql, allowed_starts=("SELECT", "WITH", "DECLARE"), allow_batches=True)

    @pytest.mark.parametrize("allow_batches", [False, True])
    @pytest.mark.parametrize(
        "sql",
        [
            # T-SQL and PostgreSQL nest block comments; the first */ must not end the comment.
            "/* /* */ SELECT '*/ UPDATE t SET x = 1 SELECT 1 --'",
            "SELECT 1 /* /* */ ' */ DROP TABLE t --'",
            "SELECT 1 /*/*/ SELECT 2",
            # A ] inside a bracketed identifier is written ]]; the first ] must not end it.
            "SELECT 1 AS [a]]'] UPDATE t SET x = 1 SELECT 'z'",
            "SELECT 1 AS [a]]] UPDATE t SET x = 1 --]",
            # MySQL and MariaDB run the body of these comments.
            "SELECT 1 /*! INTO OUTFILE 'f' */",
            "SELECT 1 /*!50000 INTO OUTFILE 'f' */",
            "SELECT 1 /*M! INTO OUTFILE 'f' */",
        ],
    )
    def test_rejects_text_the_token_pass_could_read_differently_from_the_engine(
        self, sql, allow_batches
    ):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql, allowed_starts=("SELECT", "WITH"), allow_batches=allow_batches)

    def test_refusal_names_the_comment_problem(self):
        with pytest.raises(ReadOnlyViolation, match="nested block comment"):
            assert_read_only("SELECT 1 /* a /* b */ */")
        with pytest.raises(ReadOnlyViolation, match="executable comment"):
            assert_read_only("SELECT 1 /*! b */")

    def test_refusal_names_a_multi_word_keyword_on_one_line(self):
        with pytest.raises(ReadOnlyViolation, match="'DISABLE TRIGGER'"):
            assert_read_only("SELECT 1 disable \n trigger t", allow_batches=True)

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET x = 1",
            "DELETE FROM t",
            "DROP TABLE t",
            "CREATE TABLE t (x INT)",
            "TRUNCATE TABLE t",
            "PRAGMA writable_schema = 1",
            "SELECT * INTO copy_of_t FROM t",
            "WITH x AS (SELECT 1) DELETE FROM t",
            "SELECT 1; DROP TABLE t",
            "SELECT 1; SELECT 2",
            "EXEC sp_who",
            "SELECT * FROM t FOR UPDATE",
            "SELECT sp_helptext('x')",
            "",
            "   -- only a comment",
        ],
    )
    def test_rejects_everything_else(self, sql):
        with pytest.raises(ReadOnlyViolation):
            assert_read_only(sql)

    def test_batches_only_when_allowed_and_each_statement_is_checked(self):
        starts = ("SELECT", "DECLARE")
        assert_read_only("DECLARE @d DATE = '2026-01-01'; SELECT @d", allowed_starts=starts,
                         allow_batches=True)  # fmt: skip
        with pytest.raises(ReadOnlyViolation):
            assert_read_only("DECLARE @d INT; DROP TABLE t", allowed_starts=starts,
                             allow_batches=True)  # fmt: skip
        with pytest.raises(ReadOnlyViolation):
            assert_read_only("DECLARE @d INT; SELECT @d", allowed_starts=starts)


class TestTableRefs:
    def test_aliases_hints_and_cte_exclusion(self):
        sql = """
        WITH recent AS (SELECT agreement_id FROM agreements a WITH (NOLOCK) WHERE a.status_id = 21)
        SELECT r.agreement_id FROM recent r
        JOIN dbo.agents ag (NOLOCK) ON ag.agent_id = 'x'
        LEFT JOIN agreement_debts AS d ON d.agreement_id = r.agreement_id
        """
        refs = iter_table_refs(sql)
        by_table = {ref.table: ref for ref in refs}
        assert set(by_table) == {"agreements", "agents", "agreement_debts"}
        assert by_table["agreements"].alias == "a"
        assert by_table["agreements"].hint == "NOLOCK"
        assert by_table["agents"].hint == "NOLOCK"
        assert by_table["agreement_debts"].alias == "d"
        assert by_table["agreement_debts"].hint is None
        assert cte_names(sql) == {"recent"}

    def test_hint_with_one_level_of_nested_parentheses(self):
        sql = "SELECT 1 FROM agents a WITH (NOLOCK, INDEX(ix_agents)) JOIN debts d ON d.debt_id = 1"
        by_table = {ref.table: ref for ref in iter_table_refs(sql)}
        assert by_table["agents"].hint == "NOLOCK, INDEX(ix_agents)"
        assert by_table["debts"].hint is None

    def test_table_functions_and_subqueries_are_not_tables(self):
        sql = "SELECT * FROM (SELECT 1 AS n) q JOIN generate_series(1, 3) g ON 1 = 1"
        assert iter_table_refs(sql) == []

    def test_from_inside_functions_is_not_a_table(self):
        sql = "SELECT EXTRACT(YEAR FROM issued_at), TRIM(BOTH 'x' FROM label) FROM agreements"
        assert extract_tables(sql) == ["agreements"]

    def test_is_distinct_from_is_not_a_table(self):
        assert extract_tables("SELECT 1 FROM a WHERE a.x IS DISTINCT FROM a.y") == ["a"]

    def test_clause_words_are_not_aliases(self):
        refs = iter_table_refs("SELECT 1 FROM agreements WHERE 1 = 1 ORDER BY 1")
        assert refs[0].alias is None


class TestExtractRefs:
    def test_aliases_resolve_to_tables(self):
        sql = """
        SELECT a.amount, ag.login, s.label
        FROM agreements a
        JOIN agents ag ON ag.agent_id = a.agent_id
        JOIN agreement_statuses AS s ON s.status_id = a.status_id
        """
        assert extract_refs(sql) == [
            "agents.agent_id",
            "agents.login",
            "agreement_statuses.label",
            "agreement_statuses.status_id",
            "agreements.agent_id",
            "agreements.amount",
            "agreements.status_id",
        ]

    def test_schema_qualified_and_quoted(self):
        sql = (
            "SELECT [dbo].[agreements].amount, x.agent_id "
            "FROM [dbo].[agreements] JOIN agents x ON 1=1"
        )
        assert extract_refs(sql) == ["agents.agent_id", "agreements.amount"]

    def test_cte_and_derived_references_are_skipped(self):
        sql = (
            "WITH t AS (SELECT amount FROM agreements) "
            "SELECT t.amount, q.n FROM t, (SELECT 1 n) q"
        )
        assert extract_refs(sql) == []

    def test_literals_comments_and_numbers_are_ignored(self):
        sql = "SELECT 1.5, 'a.b' FROM agreements a -- x.y\nWHERE a.amount > 2.25"
        assert extract_refs(sql) == ["agreements.amount"]

    def test_unqualified_columns_are_not_guessed(self):
        assert extract_refs("SELECT amount FROM agreements") == []
        assert extract_tables("SELECT amount FROM agreements") == ["agreements"]
