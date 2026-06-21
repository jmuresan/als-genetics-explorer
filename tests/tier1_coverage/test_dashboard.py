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
def dashboard_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase"}}}
            }
        ]
    })
    
    populate_clinvar(conn, "SOD1", {
        "result": {
            "8877": {
                "uid": "8877",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    })
    
    populate_opentargets(conn, {
        "data": {
            "target": {
                "approvedSymbol": "SOD1",
                "associatedDiseases": {
                    "rows": [
                        {
                            "disease": {"id": "EFO_0000253", "name": "amyotrophic lateral sclerosis"},
                            "score": 0.85
                        }
                    ]
                }
            }
        }
    })
    
    populate_reactome(conn, "SOD1", [{"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}])
    
    populate_pubmed(conn, {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "SOD1 study",
                "articleids": [],
                "sortpubdate": "2019-10-01"
            }
        }
    }, "seed_gene")
    
    conn.close()
    
    generate_hypotheses(db_file, os.path.join(str(tmp_path), "outputs", "hypotheses.md"))
    
    return db_file

def test_dashboard_disclaimer(dashboard_db, monkeypatch):
    # 1. Render active disclaimers: "Research tool only. No medical advice."
    monkeypatch.setenv("ALS_DB_PATH", dashboard_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("Research tool only. No medical advice." in w for w in warnings)

def test_dashboard_genes_table(dashboard_db, monkeypatch):
    # 2. Display ranked genes list in a tabular format.
    monkeypatch.setenv("ALS_DB_PATH", dashboard_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    assert not at.exception
    assert len(at.dataframe) > 0
    df = at.dataframe[0].value
    assert "SOD1" in df["gene_symbol"].values

def test_dashboard_slider_recalculation(dashboard_db, monkeypatch):
    # 3. Adjust scoring weights via sliders and trigger dynamic table updates.
    monkeypatch.setenv("ALS_DB_PATH", dashboard_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    initial_score = at.dataframe[0].value.iloc[0]["total_score"]
    
    slider_ot = at.slider(key="weight_ot_score")
    slider_ot.set_value(0.9).run()
    
    updated_score = at.dataframe[0].value.iloc[0]["total_score"]
    assert updated_score != initial_score

def test_dashboard_gene_selection(dashboard_db, monkeypatch):
    # 4. Allow clicking on a gene to display its detailed database evidence.
    monkeypatch.setenv("ALS_DB_PATH", dashboard_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    sb = at.selectbox(key="selected_gene")
    sb.select("SOD1").run()
    
    markdown_texts = [m.value for m in at.markdown]
    assert any("Evidence details for SOD1" in text for text in markdown_texts)

def test_dashboard_hypotheses_citations(dashboard_db, monkeypatch):
    # 5. Display generated hypotheses with visible citation links.
    monkeypatch.setenv("ALS_DB_PATH", dashboard_db)
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    markdown_texts = [m.value for m in at.markdown]
    assert any("Generated Hypotheses" in text for text in markdown_texts)
    assert any("https://pubmed.ncbi.nlm.nih.gov/" in text for text in markdown_texts)
