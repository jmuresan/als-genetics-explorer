import pandas as pd
import duckdb
import networkx as nx
import os
import re
import math
from src.config import Config

def calculate_scores(db_path: str, G: nx.MultiDiGraph, config: Config) -> pd.DataFrame:
    conn = duckdb.connect(db_path)
    
    # Weights are already normalized by Config, but handle fallback just in case
    weights = config.scoring_weights or {}
    
    # 1. Fetch all genes
    genes = conn.execute("SELECT gene_symbol FROM genes").fetchall()
    gene_symbols = sorted(list(set([g[0] for g in genes if g[0]])))
    
    # Build PPI subgraph containing only 'gene' nodes and 'interacts_with' edges
    G_ppi = nx.Graph()
    for node, data in G.nodes(data=True):
        if data.get("type") == "gene":
            G_ppi.add_node(node)
    for u, v, key, data in G.edges(keys=True, data=True):
        if data.get("type") == "interacts_with":
            if G_ppi.has_node(u) and G_ppi.has_node(v):
                G_ppi.add_edge(u, v)
                
    # Calculate degree centrality on PPI subgraph
    deg_centrality = nx.degree_centrality(G_ppi)
    
    # Fetch all hypotheses to check mentions
    hypotheses_list = conn.execute("SELECT hypothesis_id, title, description, confidence FROM hypotheses").fetchall()
    
    # Calculate max pathways for normalization
    mp_row = conn.execute("SELECT MAX(c) FROM (SELECT COUNT(DISTINCT pathway_id) as c FROM gene_pathways GROUP BY gene_symbol)").fetchone()
    max_pathways = mp_row[0] if (mp_row and mp_row[0] is not None) else 1
    if max_pathways == 0:
        max_pathways = 1

    results = []
    for gene in gene_symbols:
        # A. Open Targets disease association score (MAX score across associations)
        ot_row = conn.execute("SELECT MAX(score) FROM disease_associations WHERE gene_symbol = ?", [gene]).fetchone()
        ot_score = float(ot_row[0]) if (ot_row and ot_row[0] is not None) else 0.0
        
        # B. ClinVar pathogenicity support
        cv_rows = conn.execute("SELECT clinical_significance FROM variants WHERE gene_symbol = ?", [gene]).fetchall()
        pathogenic_count = sum(1 for row in cv_rows if row[0] and "pathogenic" in str(row[0]).lower() and "conflict" not in str(row[0]).lower())
        cv_score = float(min(pathogenic_count * 0.5, 1.0))
        
        # C. Pathway Centrality (normalized count)
        pc_row = conn.execute("SELECT COUNT(DISTINCT pathway_id) FROM gene_pathways WHERE gene_symbol = ?", [gene]).fetchone()
        pathway_count = pc_row[0] if (pc_row and pc_row[0] is not None) else 0
        pathway_centrality = float(pathway_count) / max_pathways
        
        # D. STRING network centrality (degree centrality on PPI subgraph)
        centrality_score = float(deg_centrality.get(gene, 0.0) or 0.0)
        
        # E. Literature Volume
        # Unique papers count from claims and papers tables
        papers_rows = conn.execute("""
            SELECT DISTINCT pmid FROM (
                SELECT paper_id AS pmid 
                FROM claims 
                WHERE paper_id != 'not_found' 
                  AND (
                    subject = ? 
                    OR object = ? 
                    OR subject IN (SELECT variant_id FROM variants WHERE gene_symbol = ?)
                  )
                UNION
                SELECT pmid 
                FROM papers 
                WHERE pmid != 'not_found' 
                  AND (
                    lower(title) LIKE '%' || lower(?) || '%' 
                    OR lower(abstract) LIKE '%' || lower(?) || '%'
                  )
            )
        """, [gene, gene, gene, gene, gene]).fetchall()
        unique_paper_count = len(papers_rows)
        lit_score = min(unique_paper_count * 0.1, 1.0)
        
        # F. Citation/Source Quality
        claims = conn.execute("""
            SELECT claim_id, paper_id, evidence_level 
            FROM claims 
            WHERE subject = ? 
               OR object = ? 
               OR subject IN (SELECT variant_id FROM variants WHERE gene_symbol = ?)
        """, [gene, gene, gene]).fetchall()
        
        claim_qualities = []
        for claim_id, paper_id, evidence_level in claims:
            if evidence_level:
                evidence_level_str = str(evidence_level).strip().lower()
                if evidence_level_str == "curated":
                    q = 1.0
                else:
                    try:
                        q = float(evidence_level)
                        if not math.isfinite(q):
                            raise ValueError("evidence_level is not finite")
                    except ValueError:
                        if paper_id and paper_id != "not_found":
                            q = 0.8
                        else:
                            q = 0.5
            else:
                if paper_id and paper_id != "not_found":
                    q = 0.8
                else:
                    q = 0.5
            claim_qualities.append(q)
            
        if claim_qualities:
            citation_quality = sum(claim_qualities) / len(claim_qualities)
        else:
            if unique_paper_count > 0:
                citation_quality = 0.5
            else:
                citation_quality = 0.0
            
        # G. Contradiction Penalty
        # Variant conflict: ratio of benign/VUS variants to all variants
        benign_vus_count = sum(1 for row in cv_rows if row[0] and any(k in str(row[0]).lower() for k in ("benign", "vus", "uncertain")))
        variant_conflict = float(benign_vus_count) / len(cv_rows) if len(cv_rows) > 0 else 0.0
        
        # Hypothesis conflict: ratio of low-confidence hypotheses to all hypotheses containing the gene symbol
        gene_re = re.compile(rf"\b{re.escape(gene)}\b")
        associated_hypotheses = []
        for hyp_id, title, description, confidence in hypotheses_list:
            text_to_search = f"{title or ''} {description or ''}"
            if gene_re.search(text_to_search):
                associated_hypotheses.append(confidence)
                
        total_hyps = len(associated_hypotheses)
        low_conf_hyps = sum(1 for conf in associated_hypotheses if conf and str(conf).strip().lower() == 'low')
        hypothesis_conflict = float(low_conf_hyps) / total_hyps if total_hyps > 0 else 0.0
        
        contradiction_penalty = variant_conflict + hypothesis_conflict
        
        # H. Druggability score (Max Clinical Phase)
        # approved/phase 4 = 1.0, phase 3 = 0.75, phase 2 = 0.50, phase 1 = 0.25, none/pre-clinical = 0.0
        max_phase_row = conn.execute("""
            SELECT MAX(d.max_clinical_phase) 
            FROM gene_drugs gd 
            JOIN drugs d ON gd.drug_id = d.drug_id 
            WHERE gd.gene_symbol = ?
        """, [gene]).fetchone()
        
        max_phase = max_phase_row[0] if (max_phase_row and max_phase_row[0] is not None) else None
        
        if max_phase is None:
            druggability_score = 0.0
        elif max_phase >= 4.0:
            druggability_score = 1.0
        elif max_phase >= 3.0:
            druggability_score = 0.75
        elif max_phase >= 2.0:
            druggability_score = 0.50
        elif max_phase >= 1.0:
            druggability_score = 0.25
        else:
            druggability_score = 0.0
        
        # Weighted calculation
        ot_weighted = ot_score * weights.get("open_targets_association", 0.0)
        cv_weighted = cv_score * weights.get("clinvar_pathogenicity", 0.0)
        pathway_weighted = pathway_centrality * weights.get("pathway_centrality", 0.0)
        centrality_weighted = centrality_score * weights.get("string_centrality", 0.0)
        lit_weighted = lit_score * weights.get("literature_volume", 0.0)
        cit_weighted = citation_quality * weights.get("citation_quality", 0.0)
        druggability_weighted = druggability_score * weights.get("druggability", 0.0)
        pen_weighted = contradiction_penalty * weights.get("contradiction_penalty", 0.0)
        
        total_score = (ot_weighted + cv_weighted + pathway_weighted + 
                       centrality_weighted + lit_weighted + cit_weighted +
                       druggability_weighted) - pen_weighted
        
        results.append({
            "gene_symbol": gene,
            "open_targets_score": ot_score,
            "open_targets_score_weighted": ot_weighted,
            "clinvar_score": cv_score,
            "clinvar_score_weighted": cv_weighted,
            "pathway_centrality_score": pathway_centrality,
            "pathway_centrality_score_weighted": pathway_weighted,
            "centrality_score": centrality_score,
            "centrality_score_weighted": centrality_weighted,
            "literature_score": lit_score,
            "literature_score_weighted": lit_weighted,
            "citation_quality_score": citation_quality,
            "citation_quality_score_weighted": cit_weighted,
            "druggability_score": druggability_score,
            "druggability_score_weighted": druggability_weighted,
            "contradiction_penalty": contradiction_penalty,
            "contradiction_penalty_weighted": pen_weighted,
            "total_score": total_score
        })
        
    df = pd.DataFrame(results)
    if not df.empty:
        # Sort by total_score descending, and gene_symbol ascending to resolve ties deterministically
        df = df.sort_values(by=["total_score", "gene_symbol"], ascending=[False, True]).reset_index(drop=True)
        df["rank"] = df.index + 1
    else:
        df = pd.DataFrame(columns=[
            "gene_symbol", "open_targets_score", "open_targets_score_weighted",
            "clinvar_score", "clinvar_score_weighted", "pathway_centrality_score",
            "pathway_centrality_score_weighted", "centrality_score", "centrality_score_weighted",
            "literature_score", "literature_score_weighted", "citation_quality_score",
            "citation_quality_score_weighted", "druggability_score", "druggability_score_weighted",
            "contradiction_penalty", "contradiction_penalty_weighted",
            "total_score", "rank"
        ])
        
    conn.close()
    return df

def calculate_pathway_scores(db_path: str, gene_df: pd.DataFrame) -> pd.DataFrame:
    conn = duckdb.connect(db_path)
    
    rows = conn.execute("""
        SELECT gp.pathway_id, p.pathway_name, gp.gene_symbol 
        FROM gene_pathways gp 
        JOIN pathways p ON gp.pathway_id = p.pathway_id
    """).fetchall()
    
    pathway_map = {}
    for pid, pname, gene in rows:
        if pid not in pathway_map:
            pathway_map[pid] = {"pathway_name": pname, "genes": []}
        pathway_map[pid]["genes"].append(gene)
        
    gene_scores = dict(zip(gene_df["gene_symbol"], gene_df["total_score"]))
    
    results = []
    for pid, info in pathway_map.items():
        # Average only over genes that are present in gene_df (ingested genes)
        ingested_member_genes = [g for g in info["genes"] if g in gene_scores]
        if not ingested_member_genes:
            continue
        scores = [gene_scores[g] for g in ingested_member_genes]
        avg_score = sum(scores) / len(scores)
        results.append({
            "pathway_id": pid,
            "pathway_name": info["pathway_name"],
            "score": avg_score
        })
        
    df = pd.DataFrame(results)
    if not df.empty:
        # Sort by score descending, and pathway_id ascending to resolve ties deterministically
        df = df.sort_values(by=["score", "pathway_id"], ascending=[False, True]).reset_index(drop=True)
        df["rank"] = df.index + 1
    else:
        df = pd.DataFrame(columns=["pathway_id", "pathway_name", "score", "rank"])
        
    conn.close()
    return df

def export_scores(gene_df: pd.DataFrame, pathway_df: pd.DataFrame, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    gene_df.to_csv(os.path.join(output_dir, "ranked_genes.csv"), index=False)
    pathway_df.to_csv(os.path.join(output_dir, "ranked_pathways.csv"), index=False)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Calculate gene and pathway scores")
    parser.add_argument("--db", default="data/processed/als_genetics.duckdb", help="Path to DuckDB database")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--output-dir", default="outputs", help="Directory to save ranked CSV files")
    args = parser.parse_args()
    
    cfg = Config(args.config)
    from src.graph.build_graph import build_graph
    G = build_graph(args.db)
    gene_df = calculate_scores(args.db, G, cfg)
    pathway_df = calculate_pathway_scores(args.db, gene_df)
    export_scores(gene_df, pathway_df, args.output_dir)
