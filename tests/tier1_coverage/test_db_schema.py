import os
import pytest
import duckdb
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets
)

def test_create_complete_db_structure(tmp_path):
    # 1. Create the complete database structure at a test DuckDB file path.
    db_file = os.path.join(str(tmp_path), "test.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    
    # Check if tables exist
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    assert "genes" in tables
    assert "variants" in tables
    assert "disease_associations" in tables
    assert "pathways" in tables
    assert "gene_pathways" in tables
    assert "interactions" in tables
    assert "papers" in tables
    assert "ingestion_log" in tables
    assert "hypotheses" in tables
    assert "hypothesis_evidence" in tables
    conn.close()

def test_populate_genes():
    # 2. Populate genes table with standard seed data.
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    uniprot_data = {
        "results": [
            {
                "primaryAccession": "Q9Y6K5",
                "uniProtkbId": "OXT1_HUMAN",
                "genes": [{"geneName": {"value": "C9orf72"}}],
                "proteinDescription": {
                    "recommendedName": {"fullName": {"value": "C9orf72 protein"}}
                }
            }
        ]
    }
    populate_uniprot(conn, uniprot_data)
    
    res = conn.execute("SELECT ensembl_id, gene_symbol, uniprot_id, protein_description FROM genes").fetchone()
    assert res is not None
    assert res[0] == "ENSG_C9orf72"
    assert res[1] == "C9orf72"
    assert res[2] == "Q9Y6K5"
    assert res[3] == "C9orf72 protein"
    conn.close()

def test_link_variants_to_genes():
    # 3. Link variants to genes using logical schema checks.
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    # Insert a gene
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG_SOD1', 'SOD1', 'P00441')")
    
    # Insert variant referencing SOD1
    clinvar_data = {
        "result": {
            "8877": {
                "uid": "8877",
                "title": "NM_000454.4(SOD1):c.11G>A (p.Gly4Ala)",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    }
    populate_clinvar(conn, "SOD1", clinvar_data)
    
    # Check join
    res = conn.execute("""
        SELECT g.gene_symbol, v.variant_id, v.clinical_significance 
        FROM genes g 
        JOIN variants v ON g.gene_symbol = v.gene_symbol
    """).fetchone()
    assert res is not None
    assert res[0] == "SOD1"
    assert res[1] == "8877"
    assert res[2] == "Pathogenic"
    conn.close()

def test_normalize_uniprot_and_reactome():
    # 4. Normalized parsing of UniProt protein descriptions and Reactome pathway maps.
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    uniprot_data = {
        "results": [
            {
                "primaryAccession": "P00441",
                "uniProtkbId": "SODC_HUMAN",
                "genes": [{"geneName": {"value": "SOD1"}}],
                "proteinDescription": {
                    "recommendedName": {"fullName": {"value": "Superoxide dismutase [Cu-Zn]"}}
                }
            }
        ]
    }
    populate_uniprot(conn, uniprot_data)
    
    reactome_data = [
        {"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}
    ]
    populate_reactome(conn, "SOD1", reactome_data)
    
    gene = conn.execute("SELECT protein_description FROM genes WHERE gene_symbol = 'SOD1'").fetchone()
    assert gene[0] == "Superoxide dismutase [Cu-Zn]"
    
    pathway = conn.execute("""
        SELECT p.pathway_name 
        FROM pathways p 
        JOIN gene_pathways gp ON p.pathway_id = gp.pathway_id 
        WHERE gp.gene_symbol = 'SOD1'
    """).fetchone()
    assert pathway[0] == "Amyotrophic lateral sclerosis (ALS)"
    conn.close()

def test_insert_clinvar_disease_ids():
    # 5. Insert ClinVar variant records containing associated disease name.
    conn = duckdb.connect(":memory:")
    create_tables(conn)
    
    clinvar_data = {
        "result": {
            "8877": {
                "uid": "8877",
                "title": "NM_000454.4(SOD1):c.11G>A (p.Gly4Ala)",
                "clinical_significance": {"description": "Pathogenic"},
                "trait_set": [{"trait_name": "Amyotrophic lateral sclerosis"}]
            }
        }
    }
    populate_clinvar(conn, "SOD1", clinvar_data)
    
    res = conn.execute("SELECT disease_name FROM variants WHERE variant_id = '8877'").fetchone()
    assert res is not None
    assert res[0] == "Amyotrophic lateral sclerosis"
    conn.close()
