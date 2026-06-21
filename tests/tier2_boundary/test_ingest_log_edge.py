import os
import duckdb
import pytest
from src.db.schema import create_tables
from src.db.populate import log_ingestion
import threading
import time

@pytest.fixture
def db_conn():
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    yield conn
    conn.close()

class LockSimulatedConnProxy:
    def __init__(self, conn):
        self._conn = conn
        self.call_count = 0
        
    def execute(self, *args, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            raise duckdb.Error("Database is locked")
        return self._conn.execute(*args, **kwargs)
        
    def __getattr__(self, name):
        return getattr(self._conn, name)

def test_ingest_log_concurrency_lock(db_conn):
    # 1. Logging during a database lock or write conflict (must retry).
    proxy_conn = LockSimulatedConnProxy(db_conn)
    log_ingestion(proxy_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 1, "cache/path", None)
    
    # Verify that it succeeded and retried
    assert proxy_conn.call_count == 2
    res = db_conn.execute("SELECT status FROM ingestion_log").fetchall()
    assert res[0][0] == "SUCCESS"

def test_ingest_log_message_truncation(db_conn):
    # 2. Handle very large error messages or stack traces (must truncate error messages).
    long_error = "A" * 5000
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "FAILED", 0, None, long_error)
    
    res = db_conn.execute("SELECT error_message FROM ingestion_log").fetchone()[0]
    assert len(res) == 1000
    assert res == "A" * 1000

def test_ingest_log_sql_injection(db_conn):
    # 3. Handle queries containing special characters, quotes, or SQL injection vectors.
    injection_source = "'; DROP TABLE ingestion_log; --"
    injection_error = "x' OR '1'='1"
    
    log_ingestion(db_conn, injection_source, {"gene": "SOD1"}, "FAILED", 0, None, injection_error)
    
    # The table should still exist and contain the literal values
    res = db_conn.execute("SELECT source_name, error_message FROM ingestion_log").fetchone()
    assert res[0] == injection_source
    assert res[1] == injection_error

def test_ingest_log_partial_availability(db_conn):
    # 4. Log details when a source is partially unavailable (e.g. 4 success, 2 failed).
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 1, "cache/u.json", None)
    log_ingestion(db_conn, "reactome", {"gene": "SOD1"}, "SUCCESS", 5, "cache/r.json", None)
    log_ingestion(db_conn, "string", {"gene": "SOD1"}, "SUCCESS", 10, "cache/s.json", None)
    log_ingestion(db_conn, "opentargets", {"gene": "SOD1"}, "SUCCESS", 1, "cache/o.json", None)
    log_ingestion(db_conn, "clinvar", {"gene": "SOD1"}, "FAILED", 0, None, "Connection timeout")
    log_ingestion(db_conn, "pubmed", {"gene": "SOD1"}, "FAILED", 0, None, "500 Internal Server Error")
    
    successes = db_conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE status = 'SUCCESS'").fetchone()[0]
    failures = db_conn.execute("SELECT COUNT(*) FROM ingestion_log WHERE status = 'FAILED'").fetchone()[0]
    
    assert successes == 4
    assert failures == 2

def test_ingest_log_null_cache_path(db_conn):
    # 5. Empty/null cache path entries when data is fetched directly without being cached.
    log_ingestion(db_conn, "uniprot", {"gene": "SOD1"}, "SUCCESS", 1, None, None)
    
    res = db_conn.execute("SELECT cache_path FROM ingestion_log").fetchone()[0]
    assert res is None
