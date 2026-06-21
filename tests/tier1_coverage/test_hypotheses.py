import os
import pytest
import duckdb
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_pubmed
)
from src.hypotheses.generator import generate_hypotheses

@pytest.fixture
def hypothesis_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 2 genes sharing a pathway
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            },
            {
                "primaryAccession": "Q9Y6K5",
                "uniProtkbId": "OXT1_HUMAN",
                "genes": [{"geneName": {"value": "C9orf72"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "C9orf72 protein"}}}
            }
        ]
    })
    
    # Pathway
    populate_reactome(conn, "SOD1", [{"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}])
    populate_reactome(conn, "C9orf72", [{"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}])
    
    # Interaction
    populate_string(conn, [
        {"preferredName_A": "SOD1", "preferredName_B": "C9orf72", "score": 0.72}
    ])
    
    # Paper
    populate_pubmed(conn, {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "C9orf72 pathology in Amyotrophic Lateral Sclerosis",
                "articleids": [{"idtype": "doi", "value": "10.1016/j.neuron.2019.08.010"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }, "seed_gene")
    
    conn.close()
    return db_file

def test_detect_shared_pathway_convergence(hypothesis_db, tmp_path):
    # 1. Detect "Shared pathway convergence" motif and output a valid hypothesis.
    output_md = os.path.join(str(tmp_path), "outputs", "hypotheses.md")
    generate_hypotheses(hypothesis_db, output_md)
    
    conn = duckdb.connect(hypothesis_db)
    rows = conn.execute("SELECT title, description FROM hypotheses WHERE title LIKE '%Shared pathway convergence%'").fetchall()
    assert len(rows) > 0
    assert "SOD1" in rows[0][0] or "C9orf72" in rows[0][0]
    conn.close()

def test_detect_network_proximity_candidate(hypothesis_db, tmp_path):
    # 2. Detect "Network-proximity candidate" and output a candidate gene hypothesis.
    output_md = os.path.join(str(tmp_path), "outputs", "hypotheses.md")
    generate_hypotheses(hypothesis_db, output_md)
    
    conn = duckdb.connect(hypothesis_db)
    rows = conn.execute("SELECT title, description FROM hypotheses WHERE title LIKE '%Network-proximity association%'").fetchall()
    assert len(rows) > 0
    assert "SOD1" in rows[0][0]
    conn.close()

def test_citation_validation(hypothesis_db, tmp_path):
    # 3. Validate that every claim within a hypothesis maps to a valid PMID/DOI in the papers table.
    output_md = os.path.join(str(tmp_path), "outputs", "hypotheses.md")
    generate_hypotheses(hypothesis_db, output_md)
    
    conn = duckdb.connect(hypothesis_db)
    hyp_ids = [r[0] for r in conn.execute("SELECT hypothesis_id FROM hypotheses").fetchall()]
    assert len(hyp_ids) >= 3
    
    for h_id in hyp_ids:
        ev_rows = conn.execute("SELECT pmid FROM hypothesis_evidence WHERE hypothesis_id = ?", [h_id]).fetchall()
        assert len(ev_rows) > 0
        for row in ev_rows:
            pmid = row[0]
            paper = conn.execute("SELECT title FROM papers WHERE pmid = ?", [pmid]).fetchone()
            assert paper is not None
    conn.close()

def test_default_type_candidate_mechanism(hypothesis_db, tmp_path):
    # 4. Label hypotheses as "candidate mechanism" by default.
    output_md = os.path.join(str(tmp_path), "outputs", "hypotheses.md")
    generate_hypotheses(hypothesis_db, output_md)
    
    conn = duckdb.connect(hypothesis_db)
    types = [r[0] for r in conn.execute("SELECT hypothesis_type FROM hypotheses").fetchall()]
    assert len(types) > 0
    for t in types:
        assert t == "candidate mechanism"
    conn.close()

def test_output_at_least_three_hypotheses(hypothesis_db, tmp_path):
    # 5. Output at least 3 valid hypotheses and write to outputs/hypotheses.md.
    output_md = os.path.join(str(tmp_path), "outputs", "hypotheses.md")
    generate_hypotheses(hypothesis_db, output_md)
    
    assert os.path.exists(output_md)
    with open(output_md, "r", encoding="utf-8") as f:
        content = f.read()
    assert content.count("## HYP-") >= 3
