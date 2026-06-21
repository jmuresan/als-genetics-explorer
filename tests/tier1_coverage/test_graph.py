import os
import pytest
import duckdb
import xml.etree.ElementTree as ET
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets,
    populate_pubmed
)
from src.graph.build_graph import build_graph, export_graph

@pytest.fixture
def populated_db(tmp_path):
    db_file = os.path.join(str(tmp_path), "test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # 1. Populate a gene
    populate_uniprot(conn, {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Superoxide dismutase [Cu-Zn]"}}}
            }
        ]
    })
    
    # 2. Populate variant
    populate_clinvar(conn, "SOD1", {
        "result": {
            "8877": {
                "uid": "8877",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    })
    
    # 3. Populate disease association
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
    
    # 4. Populate pathway
    populate_reactome(conn, "SOD1", [
        {"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}
    ])
    
    # 5. Populate interaction
    populate_string(conn, [
        {"preferredName_A": "SOD1", "preferredName_B": "CCS", "score": 0.999}
    ])
    
    # 6. Populate paper
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
    
    # 7. Add dummy hypothesis and evidence
    conn.execute("INSERT INTO hypotheses VALUES ('H1', 'SOD1 Hypothesis', 'Desc', 'High', 'candidate mechanism')")
    conn.execute("INSERT INTO hypothesis_evidence (hypothesis_id, pmid) VALUES ('H1', '31567890')")

    
    conn.close()
    return db_file

def test_node_types(populated_db):
    # 1. Create nodes for all 5 required types: gene, variant, disease, pathway, paper.
    G = build_graph(populated_db)
    
    node_types = {data.get("type") for node, data in G.nodes(data=True)}
    assert "gene" in node_types
    assert "variant" in node_types
    assert "disease" in node_types
    assert "pathway" in node_types
    assert "paper" in node_types

def test_edge_types(populated_db):
    # 2. Create edges for all 6 required types: associated_with_disease, has_variant, participates_in_pathway, interacts_with, cited_by, supports_claim.
    G = build_graph(populated_db)
    
    edge_types = {data.get("type") for u, v, key, data in G.edges(keys=True, data=True)}
    assert "associated_with_disease" in edge_types
    assert "has_variant" in edge_types
    assert "participates_in_pathway" in edge_types
    assert "interacts_with" in edge_types
    assert "cited_by" in edge_types
    assert "supports_claim" in edge_types

def test_edge_weights(populated_db):
    # 3. Attach weights to interacts_with edges based on STRING confidence scores.
    G = build_graph(populated_db)
    found_interacts = False
    for u, v, key, data in G.edges(keys=True, data=True):
        if data.get("type") == "interacts_with":
            found_interacts = True
            assert isinstance(data.get("weight"), float)
            assert data.get("weight") == 0.999
    assert found_interacts

def test_export_graphml(populated_db, tmp_path):
    # 4. Generate and write output to outputs/als_knowledge_graph.graphml.
    G = build_graph(populated_db)
    output_file = os.path.join(str(tmp_path), "outputs", "als_knowledge_graph.graphml")
    export_graph(G, output_file)
    assert os.path.exists(output_file)
    assert os.path.getsize(output_file) > 0

def test_valid_xml_graphml(populated_db, tmp_path):
    # 5. Parse generated GraphML to ensure it is valid XML.
    G = build_graph(populated_db)
    output_file = os.path.join(str(tmp_path), "outputs", "als_knowledge_graph.graphml")
    export_graph(G, output_file)
    
    try:
        tree = ET.parse(output_file)
        root = tree.getroot()
        assert root is not None
    except ET.ParseError as e:
        pytest.fail(f"GraphML output is not valid XML: {e}")
