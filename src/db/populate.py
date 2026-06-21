import json
import duckdb
from typing import Dict, Any, Optional, List
import re
import time

# ALS disease ids in Open Targets (EFO + MONDO incl. familial/sporadic subtypes). Used to
# keep only the ALS-relevant rows out of a target's full associatedDiseases page.
ALS_DISEASE_IDS = {"EFO_0000253", "MONDO_0004976", "EFO_0001356", "EFO_0001357"}

# Open Targets reports maximumClinicalStage as a string enum. Map it to the numeric clinical
# phase (0-4) stored in drugs.max_clinical_phase, mirroring the legacy phase semantics.
STAGE_TO_PHASE = {
    "APPROVAL": 4.0, "PHASE_IV": 4.0, "PHASE_III": 3.0, "PHASE_II": 2.0,
    "PHASE_I": 1.0, "EARLY_PHASE_I": 0.5, "PRECLINICAL": 0.0, "PHASE_0": 0.0,
}

def stage_to_phase(stage: Optional[str]) -> Optional[float]:
    if not stage:
        return None
    return STAGE_TO_PHASE.get(str(stage).strip().upper())

def log_ingestion(conn: duckdb.DuckDBPyConnection, source_name: str, query_params: Optional[Dict[str, Any]], status: str, record_count: int, cache_path: Optional[str], error_message: Optional[str]):
    serialized_params = json.dumps(query_params) if query_params else "{}"
    
    # Truncate error message if it's too long
    if error_message:
        error_message = error_message[:1000]
        
    for attempt in range(5):
        try:
            conn.execute("""
            INSERT INTO ingestion_log (source_name, query_params, status, record_count, cache_path, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            """, [source_name, serialized_params, status, record_count, cache_path, error_message])
            break
        except duckdb.Error as e:
            if "lock" in str(e).lower() or "transaction" in str(e).lower():
                time.sleep(0.05)
                continue
            raise e

def populate_uniprot(conn: duckdb.DuckDBPyConnection, data: Dict[str, Any]):
    results = data.get("results", [])
    for entry in results:
        uniprot_id = entry.get("primaryAccession")
        gene_symbol = None
        genes = entry.get("genes", [])
        if genes:
            gene_symbol = genes[0].get("geneName", {}).get("value")
        protein_description = None
        desc = entry.get("proteinDescription", {})
        if "recommendedName" in desc:
            protein_description = desc["recommendedName"].get("fullName", {}).get("value")
            
        ensembl_id = f"ENSG_{gene_symbol}" if gene_symbol else f"ENSG_{uniprot_id}"
        
        conn.execute("""
        INSERT OR REPLACE INTO genes (ensembl_id, gene_symbol, uniprot_id, protein_description)
        VALUES (?, ?, ?, ?)
        """, [ensembl_id, gene_symbol, uniprot_id, protein_description])

def extract_pmids(data: Any) -> List[str]:
    pmids = set()
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() in ("pmid", "pubid", "pubmedid") and isinstance(v, (str, int)):
                val = str(v).strip()
                if val.isdigit():
                    pmids.add(val)
            elif k.lower() == "literature" and isinstance(v, list):
                for item in v:
                    if isinstance(item, (str, int)):
                        val = str(item).strip()
                        if val.isdigit():
                            pmids.add(val)
            elif isinstance(v, (dict, list)):
                pmids.update(extract_pmids(v))
    elif isinstance(data, list):
        for item in data:
            pmids.update(extract_pmids(item))
    return sorted(pmids)

def normalize_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = doi.strip()
    doi = re.sub(r"^(?:https?://)?(?:(?:dx\.)?doi\.org/|doi:)", "", doi, flags=re.IGNORECASE)
    return doi.lower().strip()

def normalize_title(title: str) -> str:
    if not title:
        return ""
    return "".join(c for c in title.lower() if c.isalnum())

def insert_or_merge_paper(conn: duckdb.DuckDBPyConnection, pmid: Optional[str], doi: Optional[str], title: Optional[str], abstract: Optional[str], pub_date: Optional[str], ingestion_reason: str):
    # Normalize DOI and Title
    norm_doi = normalize_doi(doi) if doi else ""
    norm_title = normalize_title(title) if title else ""
    
    # 1. Look up existing papers in DB
    existing_papers = conn.execute("SELECT pmid, doi, title, ingestion_reason FROM papers").fetchall()
    
    match_pmid = None
    existing_reason = ""
    for row in existing_papers:
        e_pmid, e_doi, e_title, e_reason = row
        e_norm_doi = normalize_doi(e_doi) if e_doi else ""
        e_norm_title = normalize_title(e_title) if e_title else ""
        
        is_dup = False
        if pmid and e_pmid == pmid:
            is_dup = True
        elif norm_doi and e_norm_doi == norm_doi:
            is_dup = True
        elif norm_title and e_norm_title == norm_title:
            is_dup = True
            
        if is_dup:
            match_pmid = e_pmid
            existing_reason = e_reason
            break
            
    if match_pmid:
        # Merge ingestion reasons
        reasons_list = []
        if existing_reason:
            reasons_list = [r.strip() for r in existing_reason.split(",") if r.strip()]
        if ingestion_reason:
            new_reasons = [r.strip() for r in ingestion_reason.split(",") if r.strip()]
            for nr in new_reasons:
                if nr not in reasons_list:
                    reasons_list.append(nr)
        new_reason = ",".join(reasons_list)
        
        conn.execute("""
        UPDATE papers 
        SET ingestion_reason = ?
        WHERE pmid = ?
        """, [new_reason, match_pmid])

        if pmid and pmid != match_pmid:
            conn.execute("""
            UPDATE claims
            SET paper_id = ?
            WHERE paper_id = ?
            """, [match_pmid, pmid])
    else:
        conn.execute("""
        INSERT INTO papers (pmid, doi, title, abstract, pub_date, ingestion_reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """, [pmid, doi, title, abstract, pub_date, ingestion_reason])

    return match_pmid if match_pmid else pmid

def populate_string(conn: duckdb.DuckDBPyConnection, data: Any):
    if not isinstance(data, list):
        return
    for item in data:
        gene_a = item.get("preferredName_A")
        gene_b = item.get("preferredName_B")
        score = item.get("score")
        if not gene_a or not gene_b:
            continue
        
        # Sort interacting genes alphabetically (to prevent symmetric duplicates)
        gene_a, gene_b = sorted([gene_a, gene_b])
        
        conn.execute("""
        INSERT OR REPLACE INTO interactions (gene_a, gene_b, confidence_score)
        VALUES (?, ?, ?)
        """, [gene_a, gene_b, score])
        
        # Generate claims linked to 'not_found'
        claim_id = f"claim_string_{gene_a}_{gene_b}"
        conn.execute("""
        INSERT OR REPLACE INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
        VALUES (?, ?, ?, ?, ?, ?)
        """, [claim_id, 'not_found', gene_a, 'interacts_with', gene_b, str(score)])

def populate_reactome(conn: duckdb.DuckDBPyConnection, gene_symbol: str, data: Any):
    if not isinstance(data, list):
        return
    for item in data:
        pathway_id = item.get("stId")
        pathway_name = item.get("displayName")
        if not pathway_id:
            continue
            
        conn.execute("""
        INSERT OR REPLACE INTO pathways (pathway_id, pathway_name)
        VALUES (?, ?)
        """, [pathway_id, pathway_name])
        
        conn.execute("""
        INSERT OR REPLACE INTO gene_pathways (gene_symbol, pathway_id)
        VALUES (?, ?)
        """, [gene_symbol, pathway_id])
        
        # Extract PMIDs and generate claims
        pmids = extract_pmids(item)
        if not pmids:
            pmids = ['not_found']
            
        for pmid in pmids:
            claim_id = f"claim_reactome_{gene_symbol}_{pathway_id}_{pmid}"
            conn.execute("""
            INSERT OR REPLACE INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
            VALUES (?, ?, ?, ?, ?, ?)
            """, [claim_id, pmid, gene_symbol, 'associated_with_pathway', pathway_id, 'curated'])

def populate_clinvar(conn: duckdb.DuckDBPyConnection, gene_symbol: str, summary_data: Any):
    result = summary_data.get("result", {})
    for uid, info in result.items():
        if uid == "uids":
            continue
        # Prefer the canonical ClinVar Variation accession (VCV...) as the variant id; it is an
        # unambiguous, directly resolvable identifier. Fall back to the numeric VariationID.
        variant_id = info.get("accession") or info.get("uid")
        # ClinVar esummary v2 moved clinical interpretation under germline_classification;
        # the legacy clinical_significance key is now present but null on most records.
        germline = info.get("germline_classification") or {}
        legacy_sig = info.get("clinical_significance") or {}
        clinical_sig = germline.get("description") or legacy_sig.get("description")
        traits = germline.get("trait_set") or info.get("trait_set") or []
        disease_name = traits[0].get("trait_name") if traits else None
        
        conn.execute("""
        INSERT OR REPLACE INTO variants (variant_id, gene_symbol, clinical_significance, disease_name)
        VALUES (?, ?, ?, ?)
        """, [variant_id, gene_symbol, clinical_sig, disease_name])
        
        # Extract PMIDs and generate claims
        pmids = extract_pmids(info)
        if not pmids:
            pmids = ['not_found']
            
        for pmid in pmids:
            claim_id = f"claim_clinvar_{variant_id}_{pmid}"
            conn.execute("""
            INSERT OR REPLACE INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
            VALUES (?, ?, ?, ?, ?, ?)
            """, [claim_id, pmid, variant_id, 'associated_with_gene', gene_symbol, clinical_sig or 'unknown'])

def populate_opentargets(conn: duckdb.DuckDBPyConnection, data: Any):
    if not isinstance(data, dict):
        return
        
    association = None
    evidences = None
    
    if "association" in data:
        association = data.get("association")
        evidences = data.get("evidences")
    elif "data" in data and "target" in data["data"]:
        association = data["data"]["target"]
        
    if not association and "target" in data:
        association = data["target"]
        
    if not association:
        if "approvedSymbol" in data:
            association = data
            
    if not association:
        return

    gene_symbol = association.get("approvedSymbol")
    if not gene_symbol:
        return
        
    rows = association.get("associatedDiseases", {}).get("rows", [])
    for row in rows:
        disease = row.get("disease", {})
        disease_id = disease.get("id")
        disease_name = disease.get("name")
        score = row.get("score")
        if not disease_id:
            continue
        # Keep only ALS association(s); associatedDiseases returns hundreds of diseases.
        if disease_id not in ALS_DISEASE_IDS and "amyotrophic lateral sclerosis" not in (disease_name or "").lower():
            continue

        conn.execute("""
        INSERT OR REPLACE INTO disease_associations (gene_symbol, disease_id, disease_name, score)
        VALUES (?, ?, ?, ?)
        """, [gene_symbol, disease_id, disease_name, score])
        
        # Extract PMIDs and generate claims
        pmids = set()
        if evidences:
            pmids.update(extract_pmids(evidences))
        else:
            pmids.update(extract_pmids(row))
            
        pmids_list = sorted(pmids)
        if not pmids_list:
            pmids_list = ['not_found']
            
        for pmid in pmids_list:
            claim_id = f"claim_opentargets_{gene_symbol}_{disease_id}_{pmid}"
            conn.execute("""
            INSERT OR REPLACE INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level)
            VALUES (?, ?, ?, ?, ?, ?)
            """, [claim_id, pmid, gene_symbol, 'associated_with_disease', disease_id, str(score)])

    # Populate drugs and gene-drug mappings from Open Targets drugAndClinicalCandidates rows.
    # Each row is {id, maxClinicalStage, drug:{id, name, maximumClinicalStage,
    # mechanismsOfAction:{rows:[{mechanismOfAction}]}}}.
    drugs_list = data.get("drugs", []) if isinstance(data, dict) else []
    for row in drugs_list:
        drug = row.get("drug") if isinstance(row, dict) else None
        if not drug:
            continue
        drug_id = drug.get("id")
        drug_name = drug.get("name")
        stage = drug.get("maximumClinicalStage") or row.get("maxClinicalStage")
        max_phase = stage_to_phase(stage)
        moa_rows = (drug.get("mechanismsOfAction") or {}).get("rows") or []
        mech = moa_rows[0].get("mechanismOfAction") if moa_rows else None
        if not drug_id:
            continue

        conn.execute("""
        INSERT OR REPLACE INTO drugs (drug_id, name, mechanism_of_action, max_clinical_phase)
        VALUES (?, ?, ?, ?)
        """, [drug_id, drug_name, mech, max_phase])

        conn.execute("""
        INSERT OR REPLACE INTO gene_drugs (gene_symbol, drug_id)
        VALUES (?, ?)
        """, [gene_symbol, drug_id])

def populate_pubmed(conn: duckdb.DuckDBPyConnection, data: Any, reason: str):
    result = data.get("result", {})
    for uid, info in result.items():
        if uid == "uids":
            continue
        pmid = info.get("uid")
        title = info.get("title")
        article_ids = info.get("articleids", [])
        doi = None
        for aid in article_ids:
            if aid.get("idtype") == "doi":
                doi = aid.get("value")
                break
        pub_date = info.get("sortpubdate")
        abstract = info.get("abstract", "")
        
        insert_or_merge_paper(conn, pmid, doi, title, abstract, pub_date, reason)


