import os
import json
import logging
from typing import Dict, Any, List, Set, Tuple
from src.config import Config
from src.ingest.cache import DiskCache
from src.ingest.client import (
    UniProtClient,
    ReactomeClient,
    OpenTargetsClient,
    ClinVarClient,
    StringClient,
    PubMedClient,
    PaperDeduplicator,
    extract_pmids_from_dict,
    extract_pmids_regex,
    parse_esummary_pubmed,
    parse_pubmed_xml
)

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("als_explorer.run_all")

def chunk_list(lst: List[Any], size: int) -> List[List[Any]]:
    """Helper to partition list into chunks of a given size."""
    return [lst[i : i + size] for i in range(0, len(lst), size)]

def run_ingestion(config_path: str | None = None) -> Dict[str, Any]:
    """Runs the data ingestion and caching pipeline."""
    # 1. Load config
    config = Config(config_path)
    logger.info(f"Loaded config. Offline mode: {config.offline_mode}")
    logger.info(f"Seed genes: {', '.join(config.seed_genes)}")

    # 2. Setup cache and clients
    cache = DiskCache(config.cache_dir, config.offline_mode)
    
    uniprot_client = UniProtClient(cache)
    reactome_client = ReactomeClient(cache)
    open_targets_client = OpenTargetsClient(cache)
    clinvar_client = ClinVarClient(cache)
    string_client = StringClient(
        cache,
        confidence_threshold=config.string_confidence_threshold,
        limit=config.string_partner_limit
    )
    pubmed_client = PubMedClient(cache)

    # Dictionary to keep track of PMID sources: pmid -> set of (reason, gene)
    pmid_origins: Dict[str, Set[Tuple[str, str]]] = {}

    def add_pmid_origin(pmid: str, reason: str, gene: str):
        pmid = str(pmid).strip()
        if pmid.isdigit():
            pmid_origins.setdefault(pmid, set()).add((reason, gene))

    # 3. Ingest data for seed genes
    logger.info("Starting ingestion for seed genes...")
    for gene in config.seed_genes:
        logger.info(f"Processing gene: {gene}")
        
        # A. UniProt & Reactome
        uniprot_id = None
        try:
            uniprot_data = uniprot_client.get_gene_details(gene)
            if uniprot_data:
                uniprot_id = uniprot_data.get("primaryAccession")
                logger.info(f"Resolved UniProt ID for {gene}: {uniprot_id}")
        except Exception as e:
            logger.error(f"Failed to fetch UniProt data for {gene}: {e}")

        if uniprot_id:
            try:
                reactome_data = reactome_client.get_pathways_for_uniprot(uniprot_id)
                # Stage 1: Extract PMIDs from Reactome
                pmids = extract_pmids_from_dict(reactome_data)
                for p in pmids:
                    add_pmid_origin(p, "reactome_reference", gene)
            except Exception as e:
                logger.error(f"Failed to fetch Reactome data for {uniprot_id}: {e}")

        # B. Open Targets
        try:
            open_targets_data = open_targets_client.fetch_gene_data(gene)
            # Stage 1: Extract PMIDs from Open Targets
            pmids = extract_pmids_from_dict(open_targets_data)
            for p in pmids:
                add_pmid_origin(p, "open_targets_reference", gene)
        except Exception as e:
            logger.error(f"Failed to fetch Open Targets data for {gene}: {e}")

        # C. ClinVar
        try:
            clinvar_data = clinvar_client.get_variants(gene)
            # Stage 1: Extract PMIDs from ClinVar
            pmids = extract_pmids_from_dict(clinvar_data)
            # Fallback regex on JSON string representation
            json_str = json.dumps(clinvar_data)
            pmids.update(extract_pmids_regex(json_str))
            for p in pmids:
                add_pmid_origin(p, "clinvar_reference", gene)
        except Exception as e:
            logger.error(f"Failed to fetch ClinVar data for {gene}: {e}")

        # D. STRING Interactions
        try:
            _ = string_client.get_interactions(gene)
        except Exception as e:
            logger.error(f"Failed to fetch STRING interactions for {gene}: {e}")

        # Stage 2: Targeted PubMed search
        try:
            search_pmids = pubmed_client.search_pubmed(gene, limit=config.pubmed_limit_per_gene)
            for p in search_pmids:
                add_pmid_origin(p, "targeted_search", gene)
        except Exception as e:
            logger.error(f"Failed to search PubMed for {gene}: {e}")

    # Stage 3 & 4: Batch Fetch & Deduplicate PubMed references
    all_pmids = sorted(pmid_origins.keys())
    logger.info(f"Total unique PMIDs extracted/searched: {len(all_pmids)}")

    deduplicator = PaperDeduplicator()

    # Batch in groups of 100 for NCBI APIs
    batches = chunk_list(all_pmids, 100)
    for idx, batch in enumerate(batches):
        logger.info(f"Processing PubMed batch {idx + 1}/{len(batches)} (size: {len(batch)})")
        
        # Fetch summaries (esummary)
        summaries_res = {}
        try:
            summaries_res = pubmed_client.fetch_summaries(batch)
        except Exception as e:
            logger.error(f"Failed to fetch PubMed summaries for batch: {e}")
        
        parsed_papers = parse_esummary_pubmed(summaries_res)
        
        # Fetch abstracts (efetch)
        abstracts_xml = ""
        try:
            abstracts_xml = pubmed_client.fetch_abstracts(batch)
        except Exception as e:
            logger.error(f"Failed to fetch PubMed abstracts for batch: {e}")

        pmid_to_abstract = parse_pubmed_xml(abstracts_xml)

        # Merge abstracts into papers and feed into Deduplicator
        for paper in parsed_papers:
            pmid = paper.get("pmid")
            if isinstance(pmid, str):
                if pmid in pmid_to_abstract:
                    paper["abstract"] = pmid_to_abstract[pmid]
                
                # Map origins/reasons
                origins = pmid_origins.get(pmid, set())
                if not origins:
                    deduplicator.add_paper(paper, "unknown", "unknown")
                else:
                    for reason, gene in origins:
                        deduplicator.add_paper(paper, reason, gene)

    unique_papers = deduplicator.get_unique_papers()
    logger.info(f"Ingestion complete. Unique papers after deduplication: {len(unique_papers)}")

    # Write deduplicated papers to data/processed/
    processed_dir = os.path.dirname(config.config_path)
    # Check if config says db_path or use a standard location
    dest_dir = os.path.join(processed_dir, "data", "processed")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, "deduplicated_papers.json")
    
    # Save the output
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(unique_papers, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved deduplicated papers to {dest_path}")

    return {
        "unique_papers_count": len(unique_papers),
        "total_pmids_found": len(all_pmids),
        "output_path": dest_path
    }

def run_ingest(config_path: str | None = None) -> Dict[str, Any]:
    """Wrapper function to match expected API for tests."""
    return run_ingestion(config_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run the data ingestion and caching pipeline")
    parser.add_argument("--config", default=None, help="Path to config file")
    args = parser.parse_args()
    run_ingest(args.config)
