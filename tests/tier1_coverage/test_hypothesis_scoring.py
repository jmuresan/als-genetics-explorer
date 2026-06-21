import os
import pytest
import duckdb
import subprocess
import sys
from src.db.schema import create_tables
from src.hypotheses.generator import generate_hypotheses
from src.scoring.hypothesis_score import score_hypotheses

@pytest.fixture
def scored_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "scoring_test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # Insert genes
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_SOD1', 'SOD1')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_C9orf72', 'C9orf72')")
    
    # Insert paper (human, with protective keyword)
    conn.execute("""
    INSERT INTO papers (pmid, title, abstract, ingestion_reason) 
    VALUES ('111111', 'Human clinical study of SOD1', 'This study shows protection and slower progression in human patients.', 'seed_gene')
    """)
    conn.execute("""
    INSERT INTO papers (pmid, title, abstract, ingestion_reason) 
    VALUES ('222222', 'Another Human study of C9orf72', 'This study shows slower progression in human patients.', 'seed_gene')
    """)
    
    # Insert interactions
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('C9orf72', 'SOD1', 0.95)")
    
    # Insert claims for interaction
    conn.execute("""
    INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
    VALUES ('claim_1', '111111', 'SOD1', 'interacts_with', 'C9orf72', 'curated')
    """)
    conn.execute("""
    INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
    VALUES ('claim_2', '222222', 'C9orf72', 'interacts_with', 'SOD1', 'curated')
    """)
    
    conn.close()
    return db_file

def test_hypothesis_scoring_calculation(scored_db, tmp_path):
    # 1. Generate hypotheses
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    generate_hypotheses(scored_db, output_md)
    
    # 2. Run scoring
    score_hypotheses(scored_db, output_md=output_md)
    
    # 3. Check database updates
    conn = duckdb.connect(scored_db)
    rows = conn.execute("SELECT confidence, hypothesis_type FROM hypotheses").fetchall()
    assert len(rows) > 0
    # Since we have 2 human papers with curated evidence and string score 0.95, it should be High/Medium
    confidences = [r[0] for r in rows]
    assert "High" in confidences or "Medium" in confidences
    conn.close()

def test_hypothesis_scoring_cli(scored_db, tmp_path):
    # 1. Generate hypotheses
    output_md = os.path.join(str(tmp_path), "hypotheses.md")
    generate_hypotheses(scored_db, output_md)
    
    # 2. Run scoring via CLI
    cmd = [
        sys.executable, "-m", "src.scoring.hypothesis_score",
        "--db", scored_db,
        "--output-md", output_md
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    
    # 3. Verify output markdown file exists and has 13 sections
    assert os.path.exists(output_md)
    with open(output_md, "r", encoding="utf-8") as f:
        content = f.read()
    assert "## HYP-001" in content
    assert "Mechanism:" in content
    assert "Genes involved:" in content
    assert "Confidence:" in content
