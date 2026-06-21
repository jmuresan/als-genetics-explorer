import pytest
import duckdb
import datetime
from src.db.schema import create_tables
from src.db.populate import log_ingestion

@pytest.fixture
def db_conn():
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

def test_log_success(db_conn):
    # 1. Log a successful ingestion run with a SUCCESS status and correct record count.
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 2, "cache/uniprot_sod1.json", None)
    res = db_conn.execute("SELECT status, record_count FROM ingestion_log WHERE source_name = 'uniprot'").fetchone()
    assert res is not None
    assert res[0] == "SUCCESS"
    assert res[1] == 2

def test_log_failure(db_conn):
    # 2. Log a failed ingestion run with a FAILED status and the HTTP error message.
    log_ingestion(db_conn, "string", {"gene": "SOD1"}, "FAILED", 0, None, "HTTP Error 500: Internal Server Error")
    res = db_conn.execute("SELECT status, error_message FROM ingestion_log WHERE source_name = 'string'").fetchone()
    assert res is not None
    assert res[0] == "FAILED"
    assert "500" in res[1]

def test_log_cache_path(db_conn):
    # 3. Store the correct local cache path inside the cache_path column.
    path = "cache/uniprot_sod1.json"
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 1, path, None)
    res = db_conn.execute("SELECT cache_path FROM ingestion_log WHERE source_name = 'uniprot'").fetchone()
    assert res is not None
    assert res[0] == path

def test_log_created_at(db_conn):
    # 4. Record the precise created_at timestamp.
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 1, "path", None)
    res = db_conn.execute("SELECT created_at FROM ingestion_log WHERE source_name = 'uniprot'").fetchone()
    assert res is not None
    assert isinstance(res[0], datetime.datetime)

def test_log_zero_results(db_conn):
    # 5. Gracefully write ZERO_RESULTS status when an API query returns an empty list without error.
    log_ingestion(db_conn, "pubmed", {"gene": "SOD1"}, "ZERO_RESULTS", 0, "path", None)
    res = db_conn.execute("SELECT status, record_count FROM ingestion_log WHERE source_name = 'pubmed'").fetchone()
    assert res is not None
    assert res[0] == "ZERO_RESULTS"
    assert res[1] == 0
