import os
import yaml
import json
import logging
import socket
import pytest
import duckdb
import networkx as nx
from typing import Any, cast


# Import Config and Clients
from src.config import Config
from src.ingest.cache import DiskCache, OfflineCacheMissError
from src.ingest.client import (
    UniProtClient,
    ReactomeClient,
    OpenTargetsClient,
    ClinVarClient,
    StringClient,
    PubMedClient,
    PaperDeduplicator
)
from src.ingest.run_all import run_ingest

# Import DB normalizers and schema
from src.db.schema import create_tables
from src.db.populate import (
    log_ingestion,
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets,
    populate_pubmed
)

# Import Graph builder and exporter
from src.graph.build_graph import build_graph, export_graph

# Import Scoring and Hypothesis engines
from src.scoring.gene_score import calculate_scores, calculate_pathway_scores, export_scores
from src.hypotheses.generator import generate_hypotheses


# --- HELPER CLASSES & FIXTURES ---

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


@pytest.fixture
def temp_db(tmp_path):
    """Provides a fresh DuckDB file path with initialized tables."""
    db_file = os.path.join(str(tmp_path), "temp_integration.duckdb")
    conn = duckdb.connect(db_file)
    create_tables(conn)
    conn.close()
    return db_file


@pytest.fixture
def sample_config_file(tmp_path):
    """Writes a helper default configuration file."""
    config_data = {
        "seed_genes": ["SOD1", "TARDBP"],
        "api_settings": {
            "offline_mode": False,
            "cache_dir": str(tmp_path / "cache"),
            "string_db": {
                "confidence_threshold": 0.7,
                "partner_limit": 10
            },
            "pubmed": {
                "limit_per_gene": 10
            }
        },
        "scoring_weights": {
            "open_targets_association": 0.25,
            "clinvar_pathogenicity": 0.20,
            "string_centrality": 0.15,
            "literature_volume": 0.15
        }
    }
    config_file = tmp_path / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
    return str(config_file)


# --- 10 INTEGRATION / PAIRWISE COMBINATION TESTS ---

def test_combination_cache_and_offline_mode(tmp_path):
    """
    Test 1: Ingestion Cache (FEAT-002) + Offline Mode (FEAT-003)
    Verifies that cache hits work under offline mode, while cache misses raise the correct error.
    """
    cache_dir = str(tmp_path / "cache")
    
    # 1. Warm cache in online mode
    cache_online = DiskCache(cache_dir, offline_mode=False)
    client_online = UniProtClient(cache_online)
    res_online = client_online.get_gene_details("SOD1")
    assert res_online is not None
    assert res_online.get("primaryAccession") == "P00441"
    
    # 2. Query in offline mode - Cache Hit
    cache_offline = DiskCache(cache_dir, offline_mode=True)
    client_offline = UniProtClient(cache_offline)
    res_offline = client_offline.get_gene_details("SOD1")
    assert res_offline == res_online
    
    # 3. Query in offline mode - Cache Miss
    with pytest.raises(OfflineCacheMissError) as excinfo:
        client_offline.get_gene_details("TARDBP")
    assert "Offline cache miss for uniprot - search" in str(excinfo.value)


def test_combination_config_weights_and_scoring(temp_db, tmp_path):
    """
    Test 2: Configuration Engine (FEAT-001) + Scoring Engine (FEAT-007)
    Verifies that changing config weights dynamically triggers scoring recalculation and correct rankings.
    """
    conn = duckdb.connect(temp_db)
    # Populate two genes
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG001', 'SOD1', 'P00441')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG002', 'TARDBP', 'Q13148')")
    # Open Targets associations
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, score) VALUES ('SOD1', 'EFO_0000253', 0.8)")
    conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, score) VALUES ('TARDBP', 'EFO_0000253', 0.4)")
    conn.close()

    G = nx.MultiDiGraph()
    G.add_node("SOD1", type="gene")
    G.add_node("TARDBP", type="gene")

    # 1. Config weight heavily on Open Targets
    config_data_1 = {
        "seed_genes": ["SOD1", "TARDBP"],
        "api_settings": {"cache_dir": str(tmp_path / "cache")},
        "scoring_weights": {
            "open_targets_association": 1.0,
            "clinvar_pathogenicity": 0.0,
            "string_centrality": 0.0,
            "literature_volume": 0.0
        }
    }
    cfg_file = tmp_path / "config_1.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(config_data_1, f)
    config = Config(str(cfg_file))
    
    df1 = calculate_scores(temp_db, G, config)
    assert df1.loc[df1["gene_symbol"] == "SOD1", "total_score"].values[0] == 0.8
    assert df1.loc[df1["gene_symbol"] == "TARDBP", "total_score"].values[0] == 0.4
    assert df1.iloc[0]["gene_symbol"] == "SOD1"

    # 2. Config weight set to zero (failsafe fallback normalizes dynamically)
    config_data_2 = dict(config_data_1)
    config_data_2["scoring_weights"] = {
        "open_targets_association": 0.1,
        "clinvar_pathogenicity": 0.9,
    }
    cfg_file_2 = tmp_path / "config_2.yaml"
    with open(cfg_file_2, "w") as f:
        yaml.dump(config_data_2, f)
    config_2 = Config(str(cfg_file_2))
    
    df2 = calculate_scores(temp_db, G, config_2)
    # Total score should combine weights (0.1/1.0 Open Targets + 0.9/1.0 ClinVar)
    # ClinVar is 0.0 for both. So scores should be 0.8 * 0.1 = 0.08 and 0.4 * 0.1 = 0.04
    assert pytest.approx(df2.loc[df2["gene_symbol"] == "SOD1", "total_score"].values[0]) == 0.08
    assert pytest.approx(df2.loc[df2["gene_symbol"] == "TARDBP", "total_score"].values[0]) == 0.04


def test_combination_db_population_and_graph_export(temp_db, tmp_path):
    """
    Test 3: DB Population (FEAT-005) + Graph Export (FEAT-006)
    Verifies that DB normalization translates correctly to built NetworkX nodes/edges and exported GraphML XML.
    """
    conn = duckdb.connect(temp_db)
    
    # 1. Populate genes, variants, and interactions in DB
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
    populate_string(conn, [
        {"preferredName_A": "SOD1", "preferredName_B": "CCS", "score": 0.999}
    ])
    
    conn.close()

    # 2. Build networkx graph
    G = build_graph(temp_db)
    assert "SOD1" in G.nodes
    assert G.nodes["SOD1"]["type"] == "gene"
    assert "8877" in G.nodes
    assert G.nodes["8877"]["type"] == "variant"
    
    # Check edges
    edges = list(G.edges(data=True))
    assert any(e[0] == "SOD1" and e[1] == "8877" and e[2]["type"] == "has_variant" for e in edges)
    assert any(e[0] == "CCS" and e[1] == "SOD1" and e[2]["type"] == "interacts_with" for e in edges)


    # 3. Export to GraphML
    graphml_path = os.path.join(str(tmp_path), "graph.graphml")
    export_graph(G, graphml_path)
    assert os.path.exists(graphml_path)

    # Re-read and assert XML validity
    G_loaded = nx.read_graphml(graphml_path)
    assert "SOD1" in G_loaded.nodes
    assert "8877" in G_loaded.nodes


def test_combination_scoring_hypotheses(temp_db, tmp_path):
    """
    Test 4: Scoring (FEAT-007) + Hypotheses Generation (FEAT-008)
    Verifies backend updates for scoring and hypotheses generation.
    """
    conn = duckdb.connect(temp_db)
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

    # Generate hypotheses
    md_path = os.path.join(str(tmp_path), "hypotheses.md")
    generate_hypotheses(temp_db, md_path)
    assert os.path.exists(md_path)

    # Verify scores can be calculated
    G = nx.MultiDiGraph()
    G.add_node("SOD1", type="gene")
    cfg = Config()
    df = calculate_scores(temp_db, G, cfg)
    assert len(df) > 0
    assert "SOD1" in df["gene_symbol"].values


def test_combination_pubmed_dedup_and_cache_reads(tmp_path):
    """
    Test 5: PubMed Deduplication (FEAT-010) + Cache Reads (FEAT-002)
    Verifies that deduplicated, reason-merged papers can be successfully saved to and retrieved from cache.
    """
    cache = DiskCache(str(tmp_path / "cache"), offline_mode=False)
    dedup = PaperDeduplicator()

    paper1 = {"pmid": "11111", "doi": "10.1038/nature1", "title": "Main SOD1 Pathway"}
    paper2 = {"pmid": "11111", "doi": "10.1038/nature1_diff", "title": "Main SOD1 Pathway (Duplicate)"}
    paper3 = {"pmid": "22222", "doi": "10.1038/nature1", "title": "Unique title"} # DOI collision

    dedup.add_paper(paper1, "reason_uniprot", "SOD1")
    dedup.add_paper(paper2, "reason_clinvar", "SOD1")
    dedup.add_paper(paper3, "reason_pubmed", "TARDBP")

    unique_papers = dedup.get_unique_papers()
    assert len(unique_papers) == 1  # Collisions on pmid and doi collapse it to 1

    # Write deduplicated result to Cache
    params = {"query": "ALS_dedup_test"}
    cache.write("pubmed", "deduplicated_results", params, unique_papers)

    # Read back from cache
    cached_data = cache.read("pubmed", "deduplicated_results", params)
    assert cached_data is not None
    assert len(cached_data) == 1
    assert cached_data[0]["pmid"] == "11111"
    
    # Assert merged ingestion reasons are preserved
    reasons = cached_data[0]["ingestion_reasons"]
    assert any(r["reason"] == "reason_uniprot" and r["gene"] == "SOD1" for r in reasons)
    assert any(r["reason"] == "reason_clinvar" and r["gene"] == "SOD1" for r in reasons)
    assert any(r["reason"] == "reason_pubmed" and r["gene"] == "TARDBP" for r in reasons)


def test_combination_ingestion_log_and_db_schema(temp_db):
    """
    Test 6: Ingestion Logs (FEAT-004) + DB Schema Queries (FEAT-005)
    Verifies DuckDB schema constraints and SQL injection sanitization on ingestion status logging.
    """
    conn = duckdb.connect(temp_db)
    
    # 1. Log a typical SUCCESS
    log_ingestion(conn, "UniProt", {"gene": "SOD1"}, "SUCCESS", 1, "/path/cache1.json", None)
    
    # 2. Log a FAILED with long error message (truncation check) and SQL Injection payload
    sql_injection_payload = "'; DROP TABLE genes; --"
    long_error = "Error: " + ("X" * 1200)
    log_ingestion(conn, "ClinVar", {"gene": sql_injection_payload}, "FAILED", 0, None, long_error)
    
    # 3. Query DB and verify constraints
    logs = conn.execute("SELECT source_name, query_params, status, error_message, created_at FROM ingestion_log").fetchall()
    assert len(logs) == 2
    
    # Verify non-null timestamp
    assert logs[0][4] is not None
    
    # Verify error truncation
    assert len(logs[1][3]) == 1000  # Truncated to 1000 characters
    
    # Verify SQL injection payload escaped successfully
    assert "DROP TABLE" in logs[1][1]
    # Ensure genes table still exists
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    assert "genes" in tables

    conn.close()


def test_combination_config_offline_logging(temp_db, tmp_path):
    """
    Test 7: Config Engine (FEAT-001) + Offline Mode (FEAT-003) + Ingestion Logging (FEAT-004)
    Verifies that offline cache misses triggered by config are logged to DB status logs.
    """
    # 1. Setup config with offline mode enabled
    config_data = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "offline_mode": True,
            "cache_dir": str(tmp_path / "cache"),
            "string_db": {"confidence_threshold": 0.7, "partner_limit": 10},
            "pubmed": {"limit_per_gene": 10}
        }
    }
    cfg_file = tmp_path / "offline_config.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(config_data, f)
        
    config = Config(str(cfg_file))
    
    # 2. Perform ingestion that triggers cache miss
    cache = DiskCache(config.cache_dir, config.offline_mode)
    client = UniProtClient(cache)
    
    conn = duckdb.connect(temp_db)
    
    try:
        client.get_gene_details("SOD1")
    except OfflineCacheMissError as e:
        # Log failure in DB
        log_ingestion(
            conn, 
            "UniProt", 
            {"gene": "SOD1"}, 
            "FAILED", 
            0, 
            None, 
            str(e)
        )
        
    # Verify the failure was logged successfully
    logs = conn.execute("SELECT status, error_message FROM ingestion_log").fetchall()
    assert len(logs) == 1
    assert logs[0][0] == "FAILED"
    assert "Offline cache miss for uniprot - search" in logs[0][1]
    
    conn.close()


def test_combination_db_normalization_and_hypotheses(temp_db, tmp_path, monkeypatch):
    """
    Test 8: DB Normalization (FEAT-005) + Hypothesis Generator (FEAT-008)
    Verifies hypothesis citation constraints against normalized DB records.
    """
    conn = duckdb.connect(temp_db)
    
    # 1. Populate genes, pathways
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
    populate_reactome(conn, "SOD1", [
        {"stId": "R-HSA-9711123", "displayName": "Amyotrophic lateral sclerosis (ALS)"}
    ])
    conn.close()
    
    # Verify that a mismatched citation raises ValueError
    # We use proxy connection to simulate mismatched PMID in papers table
    real_conn = duckdb.connect(temp_db)
    proxy_conn = MismatchedCitationConnProxy(real_conn)
    
    import src.hypotheses.generator as generator_mod
    orig_connect = generator_mod.duckdb.connect
    
    def mock_connect(path, *args, **kwargs):
        if path == temp_db:
            return proxy_conn
        return orig_connect(path, *args, **kwargs)
        
    monkeypatch.setattr(generator_mod.duckdb, "connect", mock_connect)
    
    md_path = os.path.join(str(tmp_path), "hypotheses.md")
    with pytest.raises(ValueError, match="Hypothesis claim lacks a corresponding citation row"):
        generate_hypotheses(temp_db, md_path)
        
    # Reset monkeypatch to run normal flow
    monkeypatch.undo()
    
    # Now populate actual paper citation row
    conn = duckdb.connect(temp_db)
    populate_pubmed(conn, {
        "result": {
            "31567890": {
                "uid": "31567890",
                "title": "SOD1 and ALS",
                "articleids": [],
                "sortpubdate": "2019"
            }
        }
    }, "seed_gene")
    conn.close()
    
    # Run again. Should complete successfully.
    generate_hypotheses(temp_db, md_path)
    assert os.path.exists(md_path)
    
    # Check that hypotheses tables were populated
    conn = duckdb.connect(temp_db)
    hyp_row = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()
    assert hyp_row is not None
    hyp_count = hyp_row[0]
    ev_row = conn.execute("SELECT COUNT(*) FROM hypothesis_evidence").fetchone()
    assert ev_row is not None
    ev_count = ev_row[0]
    assert hyp_count >= 3  # Generates at least 3 hypotheses
    assert ev_count >= 3
    conn.close()


def test_combination_graph_export_and_scoring_centrality(temp_db):
    """
    Test 9: Graph Export (FEAT-006) + Scoring Engine Centrality (FEAT-007)
    Verifies that scoring centrality matches NetworkX computed degree centrality from DB populated connections.
    """
    conn = duckdb.connect(temp_db)
    
    # Populate genes
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG001', 'SOD1')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG002', 'CCS')")
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG003', 'FUS')")
    
    # Populate interactions: SOD1 - CCS, CCS - FUS (CCS has 2 connections, others have 1)
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('SOD1', 'CCS', 0.9)")
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('CCS', 'FUS', 0.8)")
    
    conn.close()

    # Build Graph
    G = build_graph(temp_db)
    
    # Compute NetworkX centrality directly
    deg_centrality = cast(dict[str, float], nx.degree_centrality(G))
    assert deg_centrality["CCS"] > deg_centrality["SOD1"]
    assert deg_centrality["CCS"] > deg_centrality["FUS"]
    
    # Run scoring engine
    config = Config() # uses default weights
    df = calculate_scores(temp_db, G, config)
    
    # Assert that centrality scores in the dataframe match the NetworkX centralities
    ccs_score = df.loc[df["gene_symbol"] == "CCS", "centrality_score"].values[0]
    sod1_score = df.loc[df["gene_symbol"] == "SOD1", "centrality_score"].values[0]
    
    assert ccs_score == deg_centrality["CCS"]
    assert sod1_score == deg_centrality["SOD1"]
    assert ccs_score > sod1_score


def test_combination_config_and_pubmed_limit(tmp_path):
    """
    Test 10: Config Engine (FEAT-001) + PubMed Ingestion (FEAT-010)
    Verifies that custom config limits successfully restrict the quantity of ingested papers.
    """
    # 1. Custom config with search limit = 2
    config_data = {
        "seed_genes": ["SOD1"],
        "api_settings": {
            "cache_dir": str(tmp_path / "cache"),
            "pubmed": {
                "limit_per_gene": 2
            }
        }
    }
    cfg_file = tmp_path / "limit_config.yaml"
    with open(cfg_file, "w") as f:
        yaml.dump(config_data, f)
        
    config = Config(str(cfg_file))
    
    # 2. Run PubMedClient search. 
    # Global interceptor mock returns a list of PMIDs, but client restricts to config limit.
    cache = DiskCache(config.cache_dir, offline_mode=False)
    client = PubMedClient(cache)
    
    pmids = client.search_pubmed("SOD1", limit=config.pubmed_limit_per_gene)
    
    # Assert query quantity is strictly bounded by the custom config setting
    assert len(pmids) <= 2
    assert len(pmids) > 0
