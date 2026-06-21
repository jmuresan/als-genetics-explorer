import os
import argparse
import duckdb
import json
import logging
from typing import Dict, Any
from src.config import Config
from src.ingest.run_all import run_ingest
from src.ingest.cache import DiskCache
from src.ingest.client import (
    UniProtClient,
    ReactomeClient,
    OpenTargetsClient,
    ClinVarClient,
    StringClient,
    PubMedClient
)
from src.db.schema import create_tables
from src.db.populate import (
    populate_uniprot,
    populate_string,
    populate_reactome,
    populate_clinvar,
    populate_opentargets,
    insert_or_merge_paper,
    log_ingestion
)
from src.graph.build_graph import build_graph, export_graph
from src.scoring.gene_score import calculate_scores, calculate_pathway_scores, export_scores
from src.hypotheses.generator import generate_hypotheses

logger = logging.getLogger("als_explorer.pipeline")

def run_pipeline(config_path: str = None, db_path: str = None, output_dir: str = None) -> Dict[str, Any]:
    # 1. Load config
    config = Config(config_path)
    if not db_path:
        db_path = os.environ.get("ALS_DB_PATH", config.get("database", {}).get("db_path", "data/processed/als_genetics.duckdb"))
    if not output_dir:
        output_dir = "outputs"

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # 2. Run ingestion
    logger.info("Step 1: Running data ingestion...")
    ingest_result = run_ingest(config.config_path)
    logger.info(f"Ingestion finished. Ingested papers count: {ingest_result.get('unique_papers_count', 0)}")

    # 3. Populate DuckDB
    logger.info("Step 2: Populating DuckDB database...")
    conn = duckdb.connect(db_path)
    create_tables(conn)

    # Honor the configured ingestion mode. With offline_mode False the clients hit live
    # endpoints and cache every raw response; with True they replay from data/raw/cache.
    cache = DiskCache(config.cache_dir, offline_mode=config.offline_mode)
    
    uniprot_client = UniProtClient(cache)
    reactome_client = ReactomeClient(cache)
    open_targets_client = OpenTargetsClient(cache)
    clinvar_client = ClinVarClient(cache)
    string_client = StringClient(cache, confidence_threshold=config.string_confidence_threshold, limit=config.string_partner_limit)
    pubmed_client = PubMedClient(cache)

    for gene in config.seed_genes:
        # UniProt
        uniprot_id = None
        try:
            uniprot_data = uniprot_client.get_gene_details(gene)
            if uniprot_data:
                populate_uniprot(conn, {"results": [uniprot_data]})
                uniprot_id = uniprot_data.get("primaryAccession")
                log_ingestion(conn, "uniprot", {"gene": gene}, "SUCCESS", 1, uniprot_client.last_cache_path, None)
            else:
                log_ingestion(conn, "uniprot", {"gene": gene}, "ZERO_RESULTS", 0, getattr(uniprot_client, "last_cache_path", None), None)
        except Exception as e:
            log_ingestion(conn, "uniprot", {"gene": gene}, "FAILED", 0, getattr(uniprot_client, "last_cache_path", None), str(e))
            logger.error(f"Error populating UniProt for {gene}: {e}")

        # Reactome
        if uniprot_id:
            try:
                reactome_data = reactome_client.get_pathways_for_uniprot(uniprot_id)
                if reactome_data:
                    populate_reactome(conn, gene, reactome_data)
                    log_ingestion(conn, "reactome", {"gene": gene}, "SUCCESS", len(reactome_data), reactome_client.last_cache_path, None)
                else:
                    log_ingestion(conn, "reactome", {"gene": gene}, "ZERO_RESULTS", 0, reactome_client.last_cache_path, None)
            except Exception as e:
                log_ingestion(conn, "reactome", {"gene": gene}, "FAILED", 0, getattr(reactome_client, "last_cache_path", None), str(e))
                logger.error(f"Error populating Reactome for {gene}: {e}")
        else:
            log_ingestion(conn, "reactome", {"gene": gene}, "FAILED", 0, None, "Missing UniProt ID for gene")

        # Open Targets
        try:
            ot_data = open_targets_client.fetch_gene_data(gene)
            association = ot_data.get("association") if ot_data else None
            rows = association.get("associatedDiseases", {}).get("rows", []) if association else []
            if ot_data and rows:
                populate_opentargets(conn, ot_data)
                log_ingestion(conn, "open_targets", {"gene": gene}, "SUCCESS", len(rows), open_targets_client.last_cache_path, None)
            else:
                log_ingestion(conn, "open_targets", {"gene": gene}, "ZERO_RESULTS", 0, getattr(open_targets_client, "last_cache_path", None), None)
        except Exception as e:
            log_ingestion(conn, "open_targets", {"gene": gene}, "FAILED", 0, getattr(open_targets_client, "last_cache_path", None), str(e))
            logger.error(f"Error populating Open Targets for {gene}: {e}")

        # ClinVar
        try:
            cv_data = clinvar_client.get_variants(gene)
            variants = cv_data.get("variants") if cv_data else None
            if cv_data and variants:
                populate_clinvar(conn, gene, {"result": variants})
                count = len([k for k in variants.keys() if k != "uids"])
                if count > 0:
                    log_ingestion(conn, "clinvar", {"gene": gene}, "SUCCESS", count, clinvar_client.last_cache_path, None)
                else:
                    log_ingestion(conn, "clinvar", {"gene": gene}, "ZERO_RESULTS", 0, clinvar_client.last_cache_path, None)
            else:
                log_ingestion(conn, "clinvar", {"gene": gene}, "ZERO_RESULTS", 0, getattr(clinvar_client, "last_cache_path", None), None)
        except Exception as e:
            log_ingestion(conn, "clinvar", {"gene": gene}, "FAILED", 0, getattr(clinvar_client, "last_cache_path", None), str(e))
            logger.error(f"Error populating ClinVar for {gene}: {e}")

        # STRING
        try:
            string_data = string_client.get_interactions(gene)
            if string_data:
                populate_string(conn, string_data)
                log_ingestion(conn, "string", {"gene": gene}, "SUCCESS", len(string_data), string_client.last_cache_path, None)
            else:
                log_ingestion(conn, "string", {"gene": gene}, "ZERO_RESULTS", 0, getattr(string_client, "last_cache_path", None), None)
        except Exception as e:
            log_ingestion(conn, "string", {"gene": gene}, "FAILED", 0, getattr(string_client, "last_cache_path", None), str(e))
            logger.error(f"Error populating STRING for {gene}: {e}")

        # PubMed search logging per seed gene
        try:
            pubmed_ids = pubmed_client.search_pubmed(gene, limit=config.pubmed_limit_per_gene)
            if pubmed_ids:
                log_ingestion(conn, "pubmed", {"gene": gene}, "SUCCESS", len(pubmed_ids), pubmed_client.last_cache_path, None)
            else:
                log_ingestion(conn, "pubmed", {"gene": gene}, "ZERO_RESULTS", 0, getattr(pubmed_client, "last_cache_path", None), None)
        except Exception as e:
            log_ingestion(conn, "pubmed", {"gene": gene}, "FAILED", 0, getattr(pubmed_client, "last_cache_path", None), str(e))
            logger.error(f"Error searching PubMed for {gene}: {e}")

    # Load papers from deduplicated_papers.json
    dedup_path = ingest_result.get("output_path")
    if dedup_path and os.path.exists(dedup_path):
        with open(dedup_path, "r", encoding="utf-8") as f:
            papers = json.load(f)
        for p in papers:
            reasons = [r["reason"] for r in p.get("ingestion_reasons", [])]
            reason = ",".join(reasons) if reasons else "seed_gene"
            canonical_pmid = insert_or_merge_paper(conn, p.get("pmid"), p.get("doi"), p.get("title"), p.get("abstract"), p.get("pubdate"), reason)

            # Populate claims linking the paper to its respective genes
            for r in p.get("ingestion_reasons", []):
                g_sym = r.get("gene")
                reason_type = r.get("reason")
                if g_sym and canonical_pmid:
                    claim_id = f"claim_paper_{canonical_pmid}_{g_sym}_{reason_type}"
                    conn.execute("""
                    INSERT OR REPLACE INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, [claim_id, canonical_pmid, g_sym, 'mentioned_in_paper', reason_type, 'literature'])


    conn.close()
    logger.info("DuckDB population completed.")

    # 4. Build and Export Graph
    logger.info("Step 3: Building and exporting graph...")
    G = build_graph(db_path)
    graphml_path = os.path.join(output_dir, "als_knowledge_graph.graphml")
    export_graph(G, graphml_path)

    # 5. Score Genes and Pathways
    logger.info("Step 4: Running scoring engine...")
    gene_df = calculate_scores(db_path, G, config)
    pathway_df = calculate_pathway_scores(db_path, gene_df)
    export_scores(gene_df, pathway_df, output_dir)

    # 6. Generate Hypotheses
    logger.info("Step 5: Generating hypotheses...")
    hyp_md_path = os.path.join(output_dir, "hypotheses.md")
    generate_hypotheses(db_path, hyp_md_path)

    # 7. Score/Validate Hypotheses
    logger.info("Step 6: Scoring hypotheses...")
    from src.scoring.hypothesis_score import score_hypotheses
    score_hypotheses(db_path, config.config_path, hyp_md_path)

    return {
        "db_path": db_path,
        "graphml_path": graphml_path,
        "ranked_genes_csv": os.path.join(output_dir, "ranked_genes.csv"),
        "ranked_pathways_csv": os.path.join(output_dir, "ranked_pathways.csv"),
        "hypotheses_md": hyp_md_path
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ALS Genetics Explorer E2E Pipeline")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--db", help="Path to DuckDB output file")
    parser.add_argument("--output-dir", help="Directory for output files")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    run_pipeline(args.config, args.db, args.output_dir)
