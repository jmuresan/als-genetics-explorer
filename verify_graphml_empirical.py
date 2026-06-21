import os
import sys
import duckdb
import networkx as nx
import xml.etree.ElementTree as ET
import pandas as pd
from src.db.schema import create_tables
from src.graph.build_graph import build_graph, export_graph

def verify_generated_graph(graphml_path):
    print("=== Step 1: Parsing GraphML File ===")
    if not os.path.exists(graphml_path):
        print(f"ERROR: GraphML file not found at {graphml_path}")
        sys.exit(1)
        
    # Check XML validity first
    try:
        tree = ET.parse(graphml_path)
        root = tree.getroot()
        print(f"SUCCESS: GraphML is valid XML. Root tag: {root.tag}")
    except Exception as e:
        print(f"ERROR: GraphML is not valid XML: {e}")
        sys.exit(1)
        
    # Parse with NetworkX
    try:
        G = nx.read_graphml(graphml_path)
        print(f"SUCCESS: NetworkX successfully loaded the graph. Graph class: {type(G)}")
    except Exception as e:
        print(f"ERROR: NetworkX failed to parse GraphML: {e}")
        sys.exit(1)

    print("\n=== Step 2: Analyzing Nodes ===")
    node_types = nx.get_node_attributes(G, 'type')
    total_nodes = len(G.nodes)
    print(f"Total nodes: {total_nodes}")
    
    allowed_node_types = {'gene', 'variant', 'disease', 'pathway', 'paper', 'hypothesis'}
    nodes_by_type = {}
    missing_type_nodes = []
    invalid_type_nodes = []
    
    for node in G.nodes:
        ntype = node_types.get(node)
        if not ntype or ntype.strip() == "" or ntype.upper() == "MISSING":
            missing_type_nodes.append((node, ntype))
        elif ntype not in allowed_node_types:
            invalid_type_nodes.append((node, ntype))
        else:
            nodes_by_type[ntype] = nodes_by_type.get(ntype, 0) + 1
            
    print("Node counts by type:")
    for ntype, count in nodes_by_type.items():
        print(f"  - {ntype}: {count}")
        
    if missing_type_nodes:
        print(f"ERROR: Found {len(missing_type_nodes)} nodes with missing/empty/MISSING type:")
        for node, ntype in missing_type_nodes[:10]:
            print(f"    Node: {node}, type: {ntype}")
    else:
        print("SUCCESS: No nodes have missing/empty/MISSING type.")
        
    if invalid_type_nodes:
        print(f"ERROR: Found {len(invalid_type_nodes)} nodes with invalid type:")
        for node, ntype in invalid_type_nodes[:10]:
            print(f"    Node: {node}, type: {ntype}")
    else:
        print("SUCCESS: All node types are valid.")

    print("\n=== Step 3: Analyzing Edges ===")
    total_edges = len(G.edges)
    print(f"Total edges: {total_edges}")
    
    allowed_edge_types = {
        'has_variant', 'associated_with_disease', 'participates_in_pathway',
        'interacts_with', 'supports_claim', 'cited_by'
    }
    edges_by_type = {}
    missing_type_edges = []
    invalid_type_edges = []
    
    # In NetworkX MultiDiGraph, edges are (u, v, key) or (u, v, data) if data=True
    for u, v, data in G.edges(data=True):
        etype = data.get('type')
        if not etype or etype.strip() == "" or etype.upper() == "MISSING":
            missing_type_edges.append((u, v, etype))
        elif etype not in allowed_edge_types:
            invalid_type_edges.append((u, v, etype))
        else:
            edges_by_type[etype] = edges_by_type.get(etype, 0) + 1
            
        # Verify attribute formats
        if etype == 'interacts_with':
            weight = data.get('weight')
            if weight is None:
                print(f"WARNING: interacts_with edge ({u} -> {v}) is missing weight")
            else:
                try:
                    float(weight)
                except ValueError:
                    print(f"ERROR: interacts_with edge ({u} -> {v}) has non-numeric weight: {weight}")
        elif etype == 'associated_with_disease':
            score = data.get('score')
            if score is not None:
                try:
                    float(score)
                except ValueError:
                    print(f"ERROR: associated_with_disease edge ({u} -> {v}) has non-numeric score: {score}")

    print("Edge counts by type:")
    for etype, count in edges_by_type.items():
        print(f"  - {etype}: {count}")
        
    if missing_type_edges:
        print(f"ERROR: Found {len(missing_type_edges)} edges with missing/empty/MISSING type:")
        for u, v, etype in missing_type_edges[:10]:
            print(f"    Edge: {u} -> {v}, type: {etype}")
    else:
        print("SUCCESS: No edges have missing/empty/MISSING type.")
        
    if invalid_type_edges:
        print(f"ERROR: Found {len(invalid_type_edges)} edges with invalid type:")
        for u, v, etype in invalid_type_edges[:10]:
            print(f"    Edge: {u} -> {v}, type: {etype}")
    else:
        print("SUCCESS: All edge types are valid.")
        
    return {
        "success": len(missing_type_nodes) == 0 and len(invalid_type_nodes) == 0 and len(missing_type_edges) == 0 and len(invalid_type_edges) == 0,
        "nodes_by_type": nodes_by_type,
        "edges_by_type": edges_by_type,
        "total_nodes": total_nodes,
        "total_edges": total_edges
    }

def test_pipeline_with_edge_cases():
    print("\n=== Step 4: Testing Pipeline with Edge Cases (Robustness Verification) ===")
    temp_db_path = "tmp_empirical_test.duckdb"
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)
        
    try:
        print("--- Scenario A: Empty Database Tables ---")
        conn = duckdb.connect(temp_db_path)
        create_tables(conn)
        conn.close()
        
        G = build_graph(temp_db_path)
        assert len(G.nodes) == 0, "Graph should be empty for empty database"
        print("SUCCESS: Empty database tables handled correctly (built empty graph without crash).")
        
        print("--- Scenario B: Database with Missing / NULL values ---")
        conn = duckdb.connect(temp_db_path)
        # Insert gene with missing symbol (NULL symbol gets skipped by schema or code?)
        # Let's see: genes table has ensembl_id (PK), gene_symbol, uniprot_id, protein_description.
        # Let's insert a gene with NULL symbol
        conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG000001', NULL, 'P12345')")
        # Insert a gene with valid symbol but NULL ensembl_id or uniprot_id
        conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG000002', 'GENEA', NULL)")
        # Insert variant with missing gene_symbol, trait, clin_sig
        conn.execute("INSERT INTO variants (variant_id, gene_symbol, clinical_significance, disease_name) VALUES ('rs123', NULL, NULL, NULL)")
        # Insert disease_association with NULL score
        conn.execute("INSERT INTO disease_associations (gene_symbol, disease_id, disease_name, score) VALUES ('GENEA', 'EFO_001', 'disease1', NULL)")
        # Insert pathways with NULL names
        conn.execute("INSERT INTO pathways (pathway_id, pathway_name) VALUES ('R-HSA-001', NULL)")
        conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('GENEA', 'R-HSA-001')")
        # Insert interaction with NULL score
        conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('GENEA', 'GENEB', NULL)")
        # Insert paper with NULL fields
        conn.execute("INSERT INTO papers (pmid, doi, title, abstract, pub_date, ingestion_reason) VALUES ('11111', NULL, NULL, NULL, NULL, 'seed_gene')")
        # Insert claim with NULL subject/object
        conn.execute("INSERT INTO claims (claim_id, paper_id, subject, predicate, object, evidence_level) VALUES ('c1', '11111', NULL, 'predicate', NULL, 'literature')")
        conn.close()
        
        G = build_graph(temp_db_path)
        print(f"SUCCESS: Built graph from database with missing/NULL values. Nodes: {len(G.nodes)}, Edges: {len(G.edges)}")
        for node, data in G.nodes(data=True):
            assert data.get('type') is not None, f"Node {node} has None type"
            
        print("--- Scenario C: Database with Duplicates ---")
        conn = duckdb.connect(temp_db_path)
        # Duplicate inserts. Since ensembl_id is PK, we can't duplicate that, but we can duplicate interaction, variant, pathway, etc.
        # Wait, does the schema have unique constraints/PKs? Let's check schema.py later or just use INSERT.
        # If we insert duplicate rows for interactions or gene_pathways:
        try:
            conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('GENEA', 'GENEB', 0.95)")
            conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('GENEA', 'GENEB', 0.95)")
        except Exception as e:
            print(f"Note: Duplicate interaction insert failed as expected or handled: {e}")
            
        try:
            conn.execute("INSERT INTO gene_pathways (gene_symbol, pathway_id) VALUES ('GENEA', 'R-HSA-001')")
        except Exception as e:
            print(f"Note: Duplicate gene_pathway insert failed as expected or handled: {e}")
            
        conn.close()
        G = build_graph(temp_db_path)
        print(f"SUCCESS: Built graph from database with duplicate rows. Nodes: {len(G.nodes)}, Edges: {len(G.edges)}")

        print("--- Scenario D: Database with Circular Interactions / Self-loops ---")
        conn = duckdb.connect(temp_db_path)
        # Self loop interaction
        conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('GENEA', 'GENEA', 0.8)")
        conn.close()
        G = build_graph(temp_db_path)
        print(f"SUCCESS: Built graph with circular interactions. Nodes: {len(G.nodes)}, Edges: {len(G.edges)}")
        assert G.has_edge("GENEA", "GENEA"), "Self-loop edge not found"

    except Exception as e:
        print(f"ERROR: Pipeline crashed during robustness test: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Empirical GraphML and pipeline robustness validator")
    parser.add_argument("--graph", default="outputs/als_knowledge_graph.graphml", help="Path to GraphML file")
    args = parser.parse_args()
    
    res = verify_generated_graph(args.graph)
    test_pipeline_with_edge_cases()
    
    if res["success"]:
        print("\nALL EMPIRICAL GRAPH VERIFICATIONS PASSED SUCCESSFULLY!")
        sys.exit(0)
    else:
        print("\nGRAPH VERIFICATION FAILED!")
        sys.exit(1)
