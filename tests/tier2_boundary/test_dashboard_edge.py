import os
import pytest
import duckdb
from streamlit.testing.v1 import AppTest
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_clinvar,
    populate_opentargets,
    populate_reactome,
    populate_pubmed
)
from src.hypotheses.generator import generate_hypotheses

@pytest.fixture
def empty_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "empty.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    conn.close()
    return db_file

@pytest.fixture
def populated_edge_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "populated_edge.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 1. Gene A
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            }
        ]
    })
    
    # 2. Gene B with extremely long description
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "Q9Y6K5",
                "genes": [{"geneName": {"value": "C9orf72"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "A" * 5000}}}
            }
        ]
    })
    
    # Paper with extremely long abstract and null DOI
    populate_pubmed(conn, {
        "result": {
            "111111": {
                "uid": "111111",
                "title": "Long Abstract Paper",
                "articleids": [],
                "sortpubdate": "2020-01-01"
            }
        }
    }, "seed_gene")
    
    conn.execute("UPDATE papers SET abstract = ?", ["B" * 10000])
    
    # Disease association
    populate_opentargets(conn, {
        "data": {
            "target": {
                "approvedSymbol": "SOD1",
                "associatedDiseases": {
                    "rows": [{"disease": {"id": "EFO_0000253", "name": "ALS"}, "score": 0.85}]
                }
            }
        }
    })
    
    # Pathway
    populate_reactome(conn, "SOD1", [{"stId": "R-HSA-123", "displayName": "Some Pathway"}])
    
    # Hypothesis with empty/null PMID link
    conn.execute("INSERT INTO hypotheses (hypothesis_id, title, description, confidence, hypothesis_type) VALUES ('HYP-001', 'Test Title', 'Test Description', 'Low', 'candidate mechanism')")
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('HYP-001', '')")
    
    conn.close()
    return db_file

def test_dashboard_extreme_sliders(populated_edge_db, monkeypatch):
    # 1. Sliders are moved to extreme values (e.g., 0.0 or 1.0) and scores re-rank successfully.
    monkeypatch.setenv("ALS_DB_PATH", populated_edge_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    at.slider(key="weight_ot_score").set_value(0.0)
    at.slider(key="weight_cv_score").set_value(1.0)
    at.slider(key="weight_string_score").set_value(0.0)
    at.slider(key="weight_lit_score").set_value(1.0)
    at.run()
    
    assert not at.exception
    df = at.dataframe[0].value
    assert "total_score" in df.columns
    assert len(df) > 0

def test_dashboard_empty_db(empty_db, monkeypatch):
    # 2. Handle empty database.
    monkeypatch.setenv("ALS_DB_PATH", empty_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    assert not at.exception
    
    monkeypatch.setenv("ALS_DB_PATH", "non_existent_file.duckdb")
    at2 = AppTest.from_file("src/dashboard/app.py")
    at2.run()
    
    errors = [e.value for e in at2.error]
    assert any("No data found" in err for err in errors)

def test_dashboard_long_texts(populated_edge_db, monkeypatch):
    # 3. Render long abstracts or text content in details panel.
    monkeypatch.setenv("ALS_DB_PATH", populated_edge_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    at.selectbox(key="selected_gene").select("C9orf72").run()
    assert not at.exception

def test_dashboard_high_frequency_updates(populated_edge_db, monkeypatch):
    # 4. Simulating high-frequency updates on sliders.
    monkeypatch.setenv("ALS_DB_PATH", populated_edge_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    for val in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        at.slider(key="weight_ot_score").set_value(val)
        at.run()
        assert not at.exception

def test_dashboard_invalid_citation_links(populated_edge_db, monkeypatch):
    # 5. Display when citations contain empty or invalid links.
    monkeypatch.setenv("ALS_DB_PATH", populated_edge_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    assert not at.exception
    markdown_texts = [m.value for m in at.markdown]
    assert not any("https://pubmed.ncbi.nlm.nih.gov//)" in text for text in markdown_texts)


def test_dashboard_corrupted_db(tmp_path, monkeypatch):
    corrupt_db_file = os.path.join(str(tmp_path), "corrupt.duckdb")
    conn = duckdb.connect(corrupt_db_file)
    # Create an incorrect schema (e.g. missing crucial tables like genes, variants)
    conn.execute("CREATE TABLE dummy (col1 VARCHAR)")
    conn.execute("INSERT INTO dummy VALUES ('test')")
    conn.close()
    
    monkeypatch.setenv("ALS_DB_PATH", corrupt_db_file)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    # Assert that no unhandled exceptions were raised
    assert not at.exception
    # Assert that an error is shown
    errors = [e.value for e in at.error]
    assert len(errors) > 0
    assert any("error" in err.lower() or "table" in err.lower() or "catalog" in err.lower() for err in errors)

