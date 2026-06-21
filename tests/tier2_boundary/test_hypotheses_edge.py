import os
import pytest
import duckdb
from src.db.schema import create_tables
from src.hypotheses.generator import generate_hypotheses

@pytest.fixture
def base_db(tmp_path):
    db_path = os.path.join(tmp_path, "hypotheses_test.duckdb")
    conn = duckdb.connect(db_path)
    create_tables(conn)
    
    # Insert a seed gene
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    
    # Insert a valid paper
    conn.execute("INSERT INTO papers (pmid, title, abstract, ingestion_reason) VALUES ('31567890', 'SOD1 in human ALS', 'This human clinical study discusses SOD1.', 'seed_gene')")
    
    conn.close()
    return db_path

class MismatchedCitationConnProxy:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, *args, **kwargs):
        if "SELECT pmid FROM papers" in sql:
            # Return a pmid that doesn't actually exist in the papers table
            mock_conn = duckdb.connect(":memory:")
            mock_conn.execute("CREATE TABLE t (pmid VARCHAR)")
            mock_conn.execute("INSERT INTO t VALUES ('999999')")
            return mock_conn.execute("SELECT pmid FROM t")
        return self._conn.execute(sql, *args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._conn, name)

def test_hypotheses_refuse_missing_citation(base_db, tmp_path):
    # 1. Refuse to generate a hypothesis if a claim lacks a corresponding citation row.
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('SOD1', 'CCS', 0.99)")
    conn.close()
    
    output_file = os.path.join(tmp_path, "hypotheses.md")
    
    # Use proxy connection to inject non-existent PMID
    real_conn = duckdb.connect(base_db)
    proxy_conn = MismatchedCitationConnProxy(real_conn)
    
    # Monkeypatch duckdb.connect to return our proxy
    import src.hypotheses.generator as generator_mod
    orig_connect = generator_mod.duckdb.connect
    
    def mock_connect(path, *args, **kwargs):
        if path == base_db:
            return proxy_conn
        return orig_connect(path, *args, **kwargs)
        
    generator_mod.duckdb.connect = mock_connect
    
    try:
        with pytest.raises(ValueError, match="Hypothesis claim lacks a corresponding citation row"):
            generate_hypotheses(base_db, output_file)
    finally:
        generator_mod.duckdb.connect = orig_connect
        real_conn.close()

def test_hypotheses_reject_protective_label(base_db, tmp_path):
    # 2. Reject "protective" label if evidence does not contain keywords.
    # It should not raise ValueError but downgrade the hypothesis type and confidence.
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_NEK1', 'NEK1')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('SOD1', 'R-HSA-123')")
    conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('NEK1', 'R-HSA-123')")
    conn.execute("INSERT INTO pathways (pathway_id, pathway_name) VALUES ('R-HSA-123', 'Protective Pathway')")
    conn.close()
    
    output_file = os.path.join(tmp_path, "hypotheses.md")
    generate_hypotheses(base_db, output_file)
    
    conn = duckdb.connect(base_db)
    hyps = conn.execute("SELECT hypothesis_type, confidence FROM hypotheses").fetchall()
    conn.close()
    assert len(hyps) > 0
    for h_type, confidence in hyps:
        assert h_type == 'candidate mechanism'
        assert confidence == 'Low'

def test_hypotheses_low_confidence_flag(base_db, tmp_path):
    # 3. Low-confidence flags: Set confidence to Low if evidence volume is low or animal-only.
    conn = duckdb.connect(base_db)
    conn.execute("UPDATE papers SET title = 'Mouse study of SOD1', abstract = 'This mouse model shows progression.'")
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('SOD1', 'CCS', 0.99)")
    conn.close()
    
    output_file = os.path.join(tmp_path, "hypotheses.md")
    generate_hypotheses(base_db, output_file)
    
    conn = duckdb.connect(base_db)
    res = conn.execute("SELECT confidence FROM hypotheses").fetchall()
    for row in res:
        assert row[0] == "Low"
    conn.close()

def test_hypotheses_zero_hypotheses_explanation(base_db, tmp_path):
    # 4. Generate zero hypotheses when the database contains no connected pathways or interactions.
    output_file = os.path.join(tmp_path, "hypotheses.md")
    generate_hypotheses(base_db, output_file)
    
    conn = duckdb.connect(base_db)
    res = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    assert res == 0
    conn.close()
    
    with open(output_file, "r") as f:
        content = f.read()
    assert "No hypotheses generated" in content

def test_hypotheses_mismatched_references(base_db, tmp_path):
    # 5. Mismatched references (e.g., papers table has PMID but DOI is missing).
    conn = duckdb.connect(base_db)
    conn.execute("UPDATE papers SET doi = NULL")
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('SOD1', 'CCS', 0.99)")
    conn.close()
    
    output_file = os.path.join(tmp_path, "hypotheses.md")
    generate_hypotheses(base_db, output_file)
    assert os.path.exists(output_file)
