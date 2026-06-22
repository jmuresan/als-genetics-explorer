import argparse
import os
import re
import duckdb
from typing import List, Dict, Any
from src.config import Config

PROTECTIVE_KEYWORDS = [
    'delayed onset', 'slower progression', 'reduced penetrance', 
    'resilience', 'protection', 'suppression', 'modifier'
]
CONTRADICTION_KEYWORDS = [
    'contradict', 'conflict', 'inconsistent', 'disputed', 'controversial'
]

def format_hypothesis_to_markdown(hyp: Dict[str, Any]) -> str:
    """Formats a single hypothesis into the exact 13-section format."""
    genes_str = ", ".join(hyp.get("genes", []))
    pathways = hyp.get("pathways", [])
    pathways_str = ", ".join(pathways) if pathways else "None"
    
    mechanism_str = hyp.get("description", "")
    
    why_matter = "Convergence of multiple ALS-associated genes or candidates suggests a critical pathological hub or regulatory axis."
    if "pathway" in hyp["title"].lower():
        why_matter = "Participating in the same biochemical pathway indicates that mutations or dysregulation in these distinct genes could lead to converged downstream pathological outcomes in ALS motor neurons."
    elif "proximity" in hyp["title"].lower():
        why_matter = "Direct interaction between these proteins suggests they form a functional complex or cascade. Dysregulation of either partner could perturb the entire complex, contributing to ALS pathogenesis."
    elif "variant" in hyp["title"].lower():
        why_matter = "Combining genetic variant evidence with pathway context highlights candidate genes that may act as functional modifiers or contributors within established ALS-associated biological pathways."
        
    sup_ev = f"Citations in the database link these entities (genes: {genes_str}) to relevant biological functions and clinical traits."
    
    if hyp.get("is_animal_only", False):
        contra = "The supporting studies are animal-only models (lacking human/patient validation)."
    elif hyp.get("has_contradiction", False):
        contra = "Literature contains conflicting findings or potential contradiction keywords."
    else:
        contra = "None"
        
    if "pathway" in hyp["title"].lower():
        prediction = f"Knockdown or mutation of {genes_str} in human motor neurons will result in shared, quantifiable disruption of the pathway phenotypic assays."
    elif "proximity" in hyp["title"].lower():
        prediction = f"Disrupting the physical interaction between {genes_str} will prevent co-localization and replicate downstream ALS-like cellular phenotypes."
    else:
        prediction = f"Targeted correction of pathogenic variants in {genes_str} will rescue functional deficits in cellular models."
        
    if "pathway" in hyp["title"].lower():
        comp_val = "Pathway membership and enrichment validated via Reactome database annotations."
    elif "proximity" in hyp["title"].lower():
        comp_val = "PPI network topological proximity and degree centrality analysis using STRING data."
    else:
        comp_val = "ClinVar pathogenicity annotations and pathway overlap checks."
        
    if "pathway" in hyp["title"].lower():
        wet_lab = "Assay downstream pathway activity or target markers in human motor neuron models."
    elif "proximity" in hyp["title"].lower():
        wet_lab = "Perform co-immunoprecipitation (Co-IP) or Bioluminescence Resonance Energy Transfer (BRET) assays to verify physical binding in human motor neurons."
    else:
        wet_lab = "Verify variant functional effects in patient-derived induced pluripotent stem cells (iPSCs)."
        
    uncertainty_reasons = []
    if len(hyp.get("citations", [])) < 2:
        uncertainty_reasons.append("sparse literature support (<2 citations)")
    if hyp.get("is_animal_only", False):
        uncertainty_reasons.append("lack of human/patient clinical validation")
    if hyp.get("has_genomic_contradiction", False):
        uncertainty_reasons.append("conflicting variant pathogenicity classifications in ClinVar")
    if hyp.get("has_lit_contradiction", False):
        uncertainty_reasons.append("contradictory terms detected in supporting literature")
        
    uncertainty_str = "Potential " + ", ".join(uncertainty_reasons) if uncertainty_reasons else "Minimal outstanding uncertainty identified."
    
    sources_str = ""
    for pmid in sorted(list(set(hyp.get("citations", [])))):
        sources_str += f"\n  - PMID: {pmid}"
        
    section = f"""## {hyp['id']}: {hyp['title']}
- **Mechanism:** {mechanism_str}
- **Genes involved:** {genes_str}
- **Pathways involved:** {pathways_str}
- **Why this might matter in ALS:** {why_matter}
- **Supporting evidence:** {sup_ev}
- **Contradicting or weak evidence:** {contra}
- **Falsifiable prediction:** {prediction}
- **Computational validation:** {comp_val}
- **High-level wet-lab concept:** {wet_lab}
- **Confidence:** {hyp['confidence']}
- **Uncertainty:** {uncertainty_str}
- **Sources**:{sources_str}
"""
    return section

def score_hypotheses(db_path: str, config_path: str | None = None, output_md: str | None = None):
    config = Config(config_path)
    conn = duckdb.connect(db_path)
    
    # Fetch all papers (excluding dummy not_found)
    papers = [r[0] for r in conn.execute("SELECT pmid FROM papers WHERE pmid <> 'not_found'").fetchall()]

    # Query the union of all gene symbols across all database tables to obtain a complete set of valid gene symbols
    gene_symbols_query = """
    SELECT gene_symbol FROM genes WHERE gene_symbol IS NOT NULL
    UNION
    SELECT gene_symbol FROM variants WHERE gene_symbol IS NOT NULL
    UNION
    SELECT gene_symbol FROM disease_associations WHERE gene_symbol IS NOT NULL
    UNION
    SELECT gene_symbol FROM gene_pathways WHERE gene_symbol IS NOT NULL
    UNION
    SELECT gene_a FROM interactions WHERE gene_a IS NOT NULL
    UNION
    SELECT gene_b FROM interactions WHERE gene_b IS NOT NULL
    """
    db_genes = conn.execute(gene_symbols_query).fetchall()
    valid_genes_map = {row[0].lower(): row[0] for row in db_genes if row[0]}

    # 1. Fetch hypotheses
    hypotheses = conn.execute("SELECT hypothesis_id, title, description, confidence, hypothesis_type FROM hypotheses").fetchall()
    if not hypotheses:
        print("No hypotheses found in database to score.")
        conn.close()
        return

    updated_hypotheses = []
    
    for h_id, title, desc, curr_conf, h_type in hypotheses:
        # 2. Fetch supporting evidence PMIDs
        evidence_pmids = [r[0] for r in conn.execute(
            "SELECT pmid FROM hypothesis_evidence WHERE hypothesis_id = ?", [h_id]
        ).fetchall() if r[0]]
        
        # 3. Reference Integrity / Citation Check
        for pmid in evidence_pmids:
            pe_row = conn.execute("SELECT COUNT(*) FROM papers WHERE pmid = ?", [pmid]).fetchone()
            assert pe_row is not None
            paper_exists = pe_row[0]
            if paper_exists == 0:
                raise ValueError(f"Hypothesis claim lacks a corresponding citation row for PMID: {pmid}")
        
        # 4. Extract paper titles and abstracts
        paper_texts = []
        for pmid in evidence_pmids:
            p_row = conn.execute("SELECT title, abstract FROM papers WHERE pmid = ?", [pmid]).fetchone()
            if p_row:
                paper_texts.append(((p_row[0] or "") + " " + (p_row[1] or "")).lower())

        # 5. Protective Label Validation
        is_protective = (h_type == "protective" or "protect" in title.lower() or "protect" in desc.lower())
        has_protective_kw = any(any(kw in pt for kw in PROTECTIVE_KEYWORDS) for pt in paper_texts)
        confidence_downgrade = False
        if is_protective:
            if not has_protective_kw:
                h_type = "candidate mechanism"
                confidence_downgrade = True
            else:
                h_type = "protective"
        else:
            if has_protective_kw:
                h_type = "protective"
            else:
                h_type = "candidate mechanism"

        # 6. Parse Genes and Pathways Involved from Title/Description
        # Use case-insensitive regex to find potential gene symbols in title and description
        genes_found = re.findall(r"\b[a-zA-Z0-9_-]{3,15}\b", f"{title} {desc}")
        genes = []
        for g in genes_found:
            g_lower = g.lower()
            if g_lower in valid_genes_map:
                genes.append(valid_genes_map[g_lower])
        genes = sorted(list(set(genes)))
        
        # Parse Pathways Involved
        pathways = []
        pathways_db = conn.execute("SELECT pathway_id, pathway_name FROM pathways").fetchall()
        for pid, pname in pathways_db:
            if pname.lower() in desc.lower() or pid in desc:
                pathways.append(f"{pname} ({pid})")
        pathways = sorted(list(set(pathways)))

        # 7. Calculate Confidence Components
        # A. Base Score based on motif type
        if "proximity" in title.lower() or "proximity" in desc.lower():
            base_score = 0.5
            if len(genes) >= 2:
                # Query interactions
                score_row = conn.execute(
                    "SELECT confidence_score FROM interactions WHERE (gene_a = ? AND gene_b = ?) OR (gene_a = ? AND gene_b = ?)",
                    [genes[0], genes[1], genes[1], genes[0]]
                ).fetchone()
                if score_row:
                    base_score = float(score_row[0])
        elif "variant" in title.lower() or "variant" in desc.lower():
            base_score = 0.8
        elif "pathway" in title.lower() or "pathway" in desc.lower():
            base_score = 0.6
        else:
            base_score = 0.2

        # B. Volume Multiplier
        n_papers = len(evidence_pmids)
        m_vol = min(0.5 + 0.1 * (n_papers - 1), 1.0)
        
        # C. Citation Quality Multiplier
        m_qual = 0.6
        if evidence_pmids:
            qual_rows = conn.execute(
                "SELECT DISTINCT evidence_level FROM claims WHERE paper_id IN ({})".format(
                    ",".join(["?"] * len(evidence_pmids))
                ), evidence_pmids
            ).fetchall()
            levels = [r[0] for r in qual_rows if r[0]]
            if any(lvl == 'curated' for lvl in levels):
                m_qual = 1.0
            elif any(lvl in ['text_mining', 'literature'] for lvl in levels):
                m_qual = 0.8

        # D. Organism Context Multiplier
        is_animal_only = True
        for pt in paper_texts:
            if "human" in pt or "patient" in pt:
                is_animal_only = False
        m_org = 1.0 if not is_animal_only else 0.5

        # E. Contradiction check
        has_lit_contradiction = any(any(cw in pt for cw in CONTRADICTION_KEYWORDS) for pt in paper_texts)
        
        has_genomic_contradiction = False
        for g in genes:
            cv_row = conn.execute("""
                SELECT COUNT(DISTINCT clinical_significance) FROM variants 
                WHERE gene_symbol = ? AND clinical_significance IN ('Pathogenic', 'Benign', 'Likely benign')
            """, [g]).fetchone()
            cv_count = cv_row[0] if cv_row else 0
            if cv_count >= 2:
                has_genomic_contradiction = True
                break

        # 8. Compute Final Score & Confidence Mapping
        final_score = base_score * m_vol * m_qual * m_org
        
        if n_papers < 2 or is_animal_only or has_lit_contradiction or has_genomic_contradiction or confidence_downgrade:
            confidence = "Low"
        elif final_score >= 0.7:
            confidence = "High"
        elif final_score >= 0.4:
            confidence = "Medium"
        else:
            confidence = "Low"

        # Update in-memory list
        updated_hypotheses.append({
            "id": h_id,
            "title": title,
            "description": desc,
            "confidence": confidence,
            "type": h_type,
            "citations": evidence_pmids,
            "genes": genes,
            "pathways": pathways,
            "is_animal_only": is_animal_only,
            "has_contradiction": has_lit_contradiction or has_genomic_contradiction,
            "has_lit_contradiction": has_lit_contradiction,
            "has_genomic_contradiction": has_genomic_contradiction
        })

        # Update Database
        conn.execute(
            "UPDATE hypotheses SET confidence = ?, hypothesis_type = ? WHERE hypothesis_id = ?",
            [confidence, h_type, h_id]
        )

    # 9. Re-render Markdown Output to keep in sync with database
    if output_md:
        os.makedirs(os.path.dirname(output_md), exist_ok=True)
        with open(output_md, "w", encoding="utf-8") as f:
            f.write("# Generated Hypotheses\n\n")
            for hyp in updated_hypotheses:
                f.write(format_hypothesis_to_markdown(hyp))

    conn.close()
    print(f"Successfully scored {len(updated_hypotheses)} hypotheses.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate and score confidence metrics for research hypotheses")
    parser.add_argument("--db", default="data/processed/als_genetics.duckdb", help="Path to DuckDB database")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--output-md", default="outputs/hypotheses.md", help="Path to update output markdown file")
    args = parser.parse_args()
    
    score_hypotheses(args.db, args.config, args.output_md)
