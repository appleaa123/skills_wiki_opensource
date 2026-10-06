from skillswiki import store

TABLES = {"skills", "cards", "learnings", "routing_log", "usage", "evals", "settings"}


def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_schema_created(tmp_home):
    with store.connect() as conn:
        assert TABLES <= _tables(conn)


def test_connect_is_idempotent(tmp_home):
    with store.connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('learning', 'on')")
    with store.connect() as conn:
        assert TABLES <= _tables(conn)
        assert conn.execute("SELECT value FROM settings WHERE key='learning'").fetchone()["value"] == "on"


def test_wal_mode(tmp_home):
    with store.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_now_is_utc_iso():
    ts = store.now()
    assert ts.endswith("+00:00") and "T" in ts


def test_settings_helpers(tmp_home):
    assert store.get_setting("learning", "on") == "on"
    store.set_setting("learning", "off")
    assert store.get_setting("learning", "on") == "off"


def test_status_check_constraint(tmp_home):
    import sqlite3

    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as conn:
            conn.execute("INSERT INTO skills (slug, name, status, path, updated_at) VALUES ('a','a','bogus','/x',?)",
                         (store.now(),))


def test_old_database_gains_copies_column(tmp_home):
    import sqlite3

    from skillswiki import paths
    old = sqlite3.connect(paths.db_path())
    old.execute("CREATE TABLE skills (slug TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',"
                " status TEXT NOT NULL, path TEXT NOT NULL, origin_path TEXT, fingerprint TEXT, has_scripts INTEGER "
                "NOT NULL DEFAULT 0, context_tokens_est INTEGER NOT NULL DEFAULT 0, adopted_at TEXT, updated_at TEXT NOT NULL)")
    old.commit()
    old.close()
    with store.connect() as conn:
        assert "copies" in {r["name"] for r in conn.execute("PRAGMA table_info(skills)")}


def test_wiring_tables_exist_and_check_values(tmp_home):
    import sqlite3

    import pytest
    from skillswiki import store
    with store.connect() as conn:
        conn.execute("INSERT INTO wiring (agent, method, self_setup, mcp, test, updated_at) "
                     "VALUES ('codex', 'hook', 0, 'done', 'untested', 'now')")
        conn.execute("INSERT INTO wiring_files (path, created, original, written_sha, parts) "
                     "VALUES ('/x', 1, NULL, 'abc', '[]')")
    with pytest.raises(sqlite3.IntegrityError), store.connect() as conn:
        conn.execute("INSERT INTO wiring (agent, method, self_setup, mcp, test, updated_at) "
                     "VALUES ('cursor', 'magic', 0, 'none', 'untested', 'now')")


def test_wiring_table_from_v030_gains_the_vouched_column(tmp_home):
    import sqlite3

    from skillswiki import paths, store
    conn = sqlite3.connect(paths.db_path())
    conn.execute("CREATE TABLE wiring (agent TEXT PRIMARY KEY, method TEXT NOT NULL, self_setup INTEGER NOT NULL "
                 "DEFAULT 0, mcp TEXT NOT NULL DEFAULT 'none', test TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO wiring VALUES ('codex', 'hook', 0, 'done', 'untested', 'now')")
    conn.commit()
    conn.close()
    with store.connect() as c:
        assert c.execute("SELECT vouched FROM wiring WHERE agent = 'codex'").fetchone()["vouched"] == 0


def test_wiring_files_from_v031_gain_the_made_dirs_column(tmp_home):
    import sqlite3

    from skillswiki import paths, store
    conn = sqlite3.connect(paths.db_path())
    conn.execute("CREATE TABLE wiring_files (path TEXT PRIMARY KEY, created INTEGER NOT NULL, original TEXT, "
                 "written_sha TEXT NOT NULL, parts TEXT NOT NULL DEFAULT '[]')")
    conn.execute("INSERT INTO wiring_files VALUES ('/x', 1, NULL, 'abc', '[]')")
    conn.commit()
    conn.close()
    with store.connect() as c:
        assert c.execute("SELECT made_dirs FROM wiring_files").fetchone()["made_dirs"] == "[]"
