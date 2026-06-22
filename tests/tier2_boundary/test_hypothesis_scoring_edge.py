import os
import pytest
import duckdb
from src.db.schema import create_tables
from src.hypotheses.generator import generate_hypotheses
from src.scoring.hypothesis_score import score_hypotheses

@pytest.fixture
def base_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "scoring_edge.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # Insert genes
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    
    # Insert paper (human, with protective keyword)
    conn.execute("""
    INSERT INTO papers (pmid, title, abstract, ingestion_reason) 
    VALUES ('111111', 'Human clinical study of SOD1', 'This study shows protection and slower progression in human patients.', 'seed_gene')
    """)
    
    conn.close()
    return db_file

def test_scoring_refuse_missing_citation(base_db, tmp_path):
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('HYP-001', 'Test', 'Test desc', 'Medium', 'candidate mechanism')")
    # Insert evidence with missing paper PMID
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('HYP-001', '999999')")
    conn.close()
    
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    with pytest.raises(ValueError, match="Hypothesis claim lacks a corresponding citation row"):
        score_hypotheses(base_db, output_md=output_md)

def test_scoring_reject_protective_label(base_db, tmp_path):
    conn = duckdb.connect(base_db)
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('HYP-001', 'Test Protective Hypothesis', 'Test desc', 'Medium', 'protective')")
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('HYP-001', '111111')")
    # Update paper to remove protective keywords
    conn.execute("UPDATE papers SET title = 'Study', abstract = 'No special keywords here.' WHERE pmid = '111111'")
    conn.close()
    
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    score_hypotheses(base_db, output_md=output_md)
    
    conn = duckdb.connect(base_db)
    hyp = conn.execute("SELECT hypothesis_type, confidence FROM hypotheses WHERE hypothesis_id = 'HYP-001'").fetchone()
    conn.close()
    assert hyp is not None
    assert hyp[0] == "candidate mechanism"
    assert hyp[1] == "Low"

def test_scoring_low_confidence_conditions(base_db, tmp_path):
    # Test animal only condition
    conn = duckdb.connect(base_db)
    # Update paper to animal-only
    conn.execute("UPDATE papers SET title = 'Mouse study of SOD1', abstract = 'This mouse model shows progression.' WHERE pmid = '111111'")
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('HYP-001', 'Test Hypothesis', 'Test desc', 'High', 'candidate mechanism')")
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('HYP-001', '111111')")
    conn.close()
    
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    score_hypotheses(base_db, output_md=output_md)
    
    conn = duckdb.connect(base_db)
    conf_row = conn.execute("SELECT confidence FROM hypotheses WHERE hypothesis_id = 'HYP-001'").fetchone()
    assert conf_row is not None
    conf = conf_row[0]
    assert conf == "Low"
    conn.close()
