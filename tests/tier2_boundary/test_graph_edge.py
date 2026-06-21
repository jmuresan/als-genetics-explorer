import os
import pytest
import duckdb
import networkx as nx
from src.db.schema import create_tables
from src.graph.build_graph import build_graph, export_graph

@pytest.fixture
def empty_db(tmp_path):
    db_path = os.path.join(tmp_path, "empty.duckdb")
    conn = duckdb.connect(db_path)
    create_tables(conn)
    conn.close()
    return db_path

@pytest.fixture
def populated_db(tmp_path):
    db_path = os.path.join(tmp_path, "populated.duckdb")
    conn = duckdb.connect(db_path)
    create_tables(conn)
    
    # Gene SOD1
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG_SOD1', 'SOD1', 'P00441')")
    
    # Isolated gene NEK1 (no variants, pathways, disease associations, or interactions)
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol, uniprot_id) VALUES ('ENSG_NEK1', 'NEK1', 'Q96S38')")
    
    # Self-loop: SOD1 interacts with SOD1
    conn.execute("INSERT INTO interactions (gene_a, gene_b, confidence_score) VALUES ('SOD1', 'SOD1', 0.99)")
    
    # Invalid rows (missing identifiers)
    conn.execute("INSERT INTO genes (ensembl_id, gene_symbol) VALUES ('ENSG_NULL', NULL)")
    
    conn.close()
    return db_path

def test_graph_empty_db(empty_db):
    # 1. Build a graph from empty database tables (verify it creates an empty graph without crashing).
    G = build_graph(empty_db)
    assert isinstance(G, nx.MultiDiGraph)
    assert len(G.nodes) == 0
    assert len(G.edges) == 0

def test_graph_isolated_nodes(populated_db):
    # 2. Handle isolated nodes (genes with no variants, pathways, or interactions).
    G = build_graph(populated_db)
    assert "NEK1" in G.nodes
    assert G.degree("NEK1") == 0

def test_graph_self_loop_edges(populated_db):
    # 3. Handle self-loop edges.
    G = build_graph(populated_db)
    assert G.has_edge("SOD1", "SOD1")
    edge_data = G.get_edge_data("SOD1", "SOD1")[0]
    assert edge_data["type"] == "interacts_with"
    assert edge_data["weight"] == 0.99

def test_graph_invalid_rows_skipped(populated_db):
    # 4. Mismatching node types or unknown attributes (must skip or default safely).
    G = build_graph(populated_db)
    assert None not in G.nodes
    assert "ENSG_NULL" not in G.nodes

def test_graph_export_write_protected_path(tmp_path):
    # 5. Verify export path is write-protected (should throw descriptive PermissionError or OSError).
    G = nx.MultiDiGraph()
    G.add_node("SOD1", type="gene")
    
    blocked_path = os.path.join(tmp_path, "blocked_dir", "graph.graphml")
    with open(os.path.join(tmp_path, "blocked_dir"), "w") as f:
        f.write("file dummy")
        
    with pytest.raises((OSError, FileExistsError, PermissionError)):
        export_graph(G, blocked_path)
