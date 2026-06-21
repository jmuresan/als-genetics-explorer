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
def interactive_db(tmp_path):
    # 1. Setup temporary database file
    db_file = os.path.join(str(tmp_path), "interactive_test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 2. Populate UniProt gene
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase 1"}}}
            }
        ]
    })
    
    # 3. Populate ClinVar variant (Pathogenic)
    populate_clinvar(conn, "SOD1", {
        "result": {
            "8877": {
                "uid": "8877",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    })
    
    # 4. Populate Open Targets disease association
    populate_opentargets(conn, {
        "data": {
            "target": {
                "approvedSymbol": "SOD1",
                "associatedDiseases": {
                    "rows": [
                        {
                            "disease": {"id": "EFO_0000253", "name": "amyotrophic lateral sclerosis"},
                            "score": 0.80
                        }
                    ]
                }
            }
        }
    })
    
    # 5. Populate Reactome pathway mapping
    populate_reactome(conn, "SOD1", [
        {"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}
    ])
    
    # 6. Populate PubMed paper citation
    populate_pubmed(conn, {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "SOD1 study in ALS patients",
                "articleids": [{"idtype": "doi", "value": "10.1038/nature123"}],
                "sortpubdate": "2019-10-01"
            }
        }
    }, "seed_gene")
    
    conn.close()
    
    # 7. Generate hypotheses
    generate_hypotheses(db_file, os.path.join(str(tmp_path), "outputs", "hypotheses.md"))
    
    return db_file

def test_dashboard_interactive_flow(interactive_db, monkeypatch):
    # Set the DB path env variable
    monkeypatch.setenv("ALS_DB_PATH", interactive_db)
    
    # Initialize the AppTest
    at = AppTest.from_file("src/dashboard/app.py")
    at.run()
    
    # --- 1. Verifying Disclaimers Show ---
    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("Research tool only. No medical advice." in w for w in warnings), "Disclaimer warning not found!"
    
    # --- 2. Verifying Data Loads from DuckDB ---
    # Ensure genes table/dataframe contains SOD1
    df = at.dataframe[0].value
    assert "SOD1" in df["gene_symbol"].values, "SOD1 was not loaded from DuckDB!"
    
    # Get initial total score of SOD1
    initial_score = df.loc[df["gene_symbol"] == "SOD1", "total_score"].values[0]
    
    # --- 3. Simulating Slider Drag / Weights Recalculation ---
    # Retrieve slider controls
    slider_ot = at.slider(key="weight_ot_score")
    slider_cv = at.slider(key="weight_cv_score")
    
    # Drag sliders (set weights and trigger recalculation)
    slider_ot.set_value(0.9)
    slider_cv.set_value(0.1)
    at.run()
    
    # Check that score recalculates dynamically
    updated_df = at.dataframe[0].value

    updated_score = updated_df.loc[updated_df["gene_symbol"] == "SOD1", "total_score"].values[0]
    assert updated_score != initial_score, "Score did not update dynamically after slider adjustment!"
    
    # --- 4. Simulating Gene Selection / Detailed Views Pop Up ---
    # Select SOD1 from the selectbox
    sb = at.selectbox(key="selected_gene")
    sb.select("SOD1").run()
    
    # Verify detailed view header pops up
    markdown_texts = [m.value for m in at.markdown]
    assert any("Evidence details for SOD1" in text for text in markdown_texts), "Detailed view header not found!"
    
    # Verify pathway display and publication display in rendered texts (markdown or text)
    rendered_texts_all = [m.value for m in at.markdown]
    if hasattr(at, "text"):
        rendered_texts_all.extend([t.value for t in at.text])
        
    assert any("Amyotrophic lateral sclerosis (ALS)" in text for text in rendered_texts_all), "Pathway mapping not displayed!"
    assert any("SOD1 study in ALS patients" in text for text in rendered_texts_all), "Publication details not displayed!"
    
    # --- 5. Checking Citations Render ---
    # Hypotheses section should show PubMed URLs
    assert any("https://pubmed.ncbi.nlm.nih.gov/31567890" in text for text in rendered_texts_all), "Citations links not rendered in markdown!"

