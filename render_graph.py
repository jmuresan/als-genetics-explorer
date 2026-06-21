"""Render the ALS gene interaction network to a PNG for the README.
Nodes are genes; edges are STRING interactions. Panel genes are highlighted and labeled,
STRING partners are drawn small. Reads the live-built DuckDB; no hand-placed data."""
import sys, yaml, duckdb, networkx as nx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DB = "data/processed/als_genetics.duckdb"
OUT = sys.argv[1] if len(sys.argv) > 1 else "outputs/graph_overview.png"
PAPER, INK, RUST, PACIFIC, LINE = "#F6F1E6", "#29251D", "#A84A22", "#2F5D6B", "#C9BCA0"

panel = set(yaml.safe_load(open("config.yaml"))["seed_genes"])
c = duckdb.connect(DB, read_only=True)

G = nx.Graph()
for g in [r[0] for r in c.execute("select gene_symbol from genes").fetchall()]:
    G.add_node(g)
for a, b, w in c.execute("select gene_a, gene_b, confidence_score from interactions").fetchall():
    if a and b:
        G.add_edge(a, b, weight=w or 0.7)

panel_nodes = [n for n in G.nodes if n in panel]
partner_nodes = [n for n in G.nodes if n not in panel]
deg = dict(G.degree())
pos = nx.spring_layout(G, k=0.38, iterations=90, seed=7)

plt.figure(figsize=(16, 12), dpi=110)
ax = plt.gca(); ax.set_facecolor(PAPER)
nx.draw_networkx_edges(G, pos, edge_color=LINE, width=0.4, alpha=0.55)
nx.draw_networkx_nodes(G, pos, nodelist=partner_nodes, node_color=PACIFIC,
                       node_size=[8 + deg[n] * 3 for n in partner_nodes], alpha=0.65, linewidths=0)
nx.draw_networkx_nodes(G, pos, nodelist=panel_nodes, node_color=RUST,
                       node_size=[140 + deg[n] * 14 for n in panel_nodes], edgecolors=INK, linewidths=0.6)
_lbl = nx.draw_networkx_labels(G, pos, labels={n: n for n in panel_nodes},
                               font_size=9, font_color=INK, font_family="monospace", font_weight="bold")
for t in _lbl.values():
    t.set_bbox(dict(facecolor=PAPER, edgecolor="none", alpha=0.7, pad=0.4))
plt.axis("off")
plt.title(f"ALS knowledge graph: {len(panel_nodes)} panel genes, {len(partner_nodes)} STRING partners, "
          f"{G.number_of_edges()} interactions",
          color=INK, fontsize=14, family="monospace")
plt.tight_layout()
plt.savefig(OUT, facecolor=PAPER, bbox_inches="tight")
print(f"wrote {OUT}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(panel_nodes)} panel")
