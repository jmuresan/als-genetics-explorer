import networkx as nx
import duckdb
import os
import math

def build_graph(db_path: str) -> nx.MultiDiGraph:
    try:
        conn = duckdb.connect(db_path)
        G = nx.MultiDiGraph()

        # Gather all potential genes from all tables to ensure node-first population
        gene_symbols = set()
        
        # 1. Genes table
        genes = conn.execute("SELECT gene_symbol, ensembl_id, uniprot_id, protein_description FROM genes").fetchall()
        gene_meta = {}
        for row in genes:
            if row[0]:
                gene_symbols.add(row[0])
                gene_meta[row[0]] = {"ensembl_id": row[1], "uniprot_id": row[2], "description": row[3]}

        # Gather from variants
        variants_rows = conn.execute("SELECT variant_id, gene_symbol, clinical_significance, disease_name FROM variants").fetchall()
        for row in variants_rows:
            if row[1]:
                gene_symbols.add(row[1])

        # Gather from disease associations
        disease_assoc_rows = conn.execute("SELECT gene_symbol, disease_id, disease_name, score FROM disease_associations").fetchall()
        for row in disease_assoc_rows:
            if row[0]:
                gene_symbols.add(row[0])

        # Gather from gene_pathways
        gene_pathways_rows = conn.execute("SELECT gene_symbol, pathway_id FROM gene_pathways").fetchall()
        for row in gene_pathways_rows:
            if row[0]:
                gene_symbols.add(row[0])

        # Gather from interactions
        interactions_rows = conn.execute("SELECT gene_a, gene_b, confidence_score FROM interactions").fetchall()
        for row in interactions_rows:
            if row[0]:
                gene_symbols.add(row[0])
            if row[1]:
                gene_symbols.add(row[1])

        # Add all unique genes first
        for symbol in sorted(gene_symbols):
            meta = gene_meta.get(symbol, {})
            G.add_node(symbol, type="gene", 
                       ensembl_id=meta.get("ensembl_id"), 
                       uniprot_id=meta.get("uniprot_id"), 
                       description=meta.get("description"))

        # Add Variant nodes first
        for row in variants_rows:
            if row[0]:
                G.add_node(row[0], type="variant", clinical_significance=row[2], disease_name=row[3])

        # Add Disease nodes first
        for row in disease_assoc_rows:
            if row[1]:
                G.add_node(row[1], type="disease", name=row[2])

        # Add Pathway nodes first
        pathways = conn.execute("SELECT pathway_id, pathway_name FROM pathways").fetchall()
        for row in pathways:
            if row[0]:
                G.add_node(row[0], type="pathway", name=row[1])
        for row in gene_pathways_rows:
            if row[1] and not G.has_node(row[1]):
                G.add_node(row[1], type="pathway")

        # Add Paper nodes first
        papers = conn.execute("SELECT pmid, doi, title, pub_date FROM papers").fetchall()
        for row in papers:
            if row[0]:
                G.add_node(row[0], type="paper", doi=row[1], title=row[2], pub_date=row[3])

        # Add Hypothesis nodes first
        hypotheses = conn.execute("SELECT hypothesis_id, title, description, confidence, hypothesis_type FROM hypotheses").fetchall()
        for row in hypotheses:
            if row[0]:
                G.add_node(row[0], type="hypothesis", title=row[1], description=row[2], confidence=row[3], hypothesis_type=row[4])

        # Now add Edges:
        
        # has_variant edges
        for row in variants_rows:
            if row[0] and row[1]:
                G.add_edge(row[1], row[0], type="has_variant")

        # associated_with_disease edges
        for row in disease_assoc_rows:
            if row[0] and row[1]:
                G.add_edge(row[0], row[1], type="associated_with_disease", score=row[3])

        # participates_in_pathway edges
        for row in gene_pathways_rows:
            if row[0] and row[1]:
                G.add_edge(row[0], row[1], type="participates_in_pathway")

        # interacts_with edges
        for row in interactions_rows:
            if row[0] and row[1]:
                G.add_edge(row[0], row[1], type="interacts_with", weight=row[2])

        # hypothesis_evidence edges (supports_claim and cited_by)
        evidence = conn.execute("SELECT hypothesis_id, pmid FROM hypothesis_evidence").fetchall()
        for row in evidence:
            if row[0] and row[1]:
                G.add_edge(row[1], row[0], type="supports_claim")
                G.add_edge(row[0], row[1], type="cited_by")

        # claims edges:
        # Enrich edges with claims evidence data and add edges from paper nodes to biological entities they support/mention.
        claims_rows = conn.execute("SELECT claim_id, paper_id, subject, predicate, object, evidence_level FROM claims").fetchall()
        for row in claims_rows:
            claim_id, paper_id, subject, predicate, object_id, evidence_level = row
            if paper_id and G.has_node(paper_id):
                if subject and G.has_node(subject):
                    G.add_edge(paper_id, subject, type="supports_claim", claim_id=claim_id, pmid=paper_id, predicate=predicate, evidence_level=evidence_level)
                if object_id and G.has_node(object_id):
                    G.add_edge(paper_id, object_id, type="supports_claim", claim_id=claim_id, pmid=paper_id, predicate=predicate, evidence_level=evidence_level)

        conn.close()

        # Sanitizer function for node/edge attributes
        def sanitize_attributes(attrs):
            clean = {}
            for k, v in attrs.items():
                if v is None:
                    continue
                if isinstance(v, str):
                    v_str = v.strip()
                    if v_str.lower() in ("none", "null", "nan", ""):
                        continue
                    # If key indicates a numeric property, ensure float conversion
                    if k in ("score", "weight") or k.endswith("_score") or k.endswith("_weight"):
                        try:
                            clean[k] = float(v_str)
                        except ValueError:
                            pass
                        continue
                    clean[k] = v_str
                elif isinstance(v, (int, float)):
                    if math.isnan(v):
                        continue
                    clean[k] = float(v)
                elif isinstance(v, bool):
                    clean[k] = v
                else:
                    clean[k] = v
            return clean

        # Sanitize graph attributes (remove None values, which crash NetworkX's GraphML exporter)
        for node, data in G.nodes(data=True):
            clean_data = sanitize_attributes(data)
            G.nodes[node].clear()
            G.nodes[node].update(clean_data)
            
        for u, v, key, data in G.edges(keys=True, data=True):
            clean_data = sanitize_attributes(data)
            G.edges[u, v, key].clear()
            G.edges[u, v, key].update(clean_data)

        return G
    except Exception:
        return nx.MultiDiGraph()

def export_graph(G: nx.MultiDiGraph, output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    # NetworkX writes the multigraph edge key as the GraphML edge id, and the key resets to 0
    # for every node pair, so most edges share id="0". Gephi (and the GraphML spec) treats the
    # id as unique and collapses the duplicates. Re-key every edge with a globally unique id so
    # all edges survive the round trip.
    H = nx.MultiDiGraph()
    H.graph.update(G.graph)
    H.add_nodes_from(G.nodes(data=True))
    for i, (u, v, d) in enumerate(G.edges(data=True)):
        H.add_edge(u, v, key="e%d" % i, **d)
    nx.write_graphml(H, output_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build and export ALS knowledge graph")
    parser.add_argument("--db", default="data/processed/als_genetics.duckdb", help="Path to DuckDB database")
    parser.add_argument("--output", default="outputs/als_knowledge_graph.graphml", help="Path to output GraphML file")
    args = parser.parse_args()
    
    G = build_graph(args.db)
    export_graph(G, args.output)
