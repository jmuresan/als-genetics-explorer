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

# Node importance = betweenness centrality (a node's role as a bridge in the network).
from typing import Any, cast
bc = cast(dict[Any, float], nx.betweenness_centrality(G))
maxbc = max(bc.values()) or 1.0
# ForceAtlas2 layout (the Gephi force model): hubs spread out, clusters separate.
# node_size feeds anti-overlap, scaled by betweenness so the big bridges get room.
_fa_size = {n: (16 if n in panel else 3) + (bc[n] / maxbc) ** 0.5 * 60 for n in G.nodes}
pos = nx.forceatlas2_layout(G, seed=7, max_iter=900, scaling_ratio=14.0, gravity=0.35, node_size=_fa_size)

def _psize(n): return 120 + (bc[n] / maxbc) ** 0.5 * 1900   # panel: big bridges pop
def _qsize(n): return 7 + (bc[n] / maxbc) ** 0.5 * 240      # partners

plt.figure(figsize=(16, 12), dpi=110)
ax = plt.gca(); ax.set_facecolor(PAPER)
nx.draw_networkx_edges(G, pos, edge_color=LINE, width=0.4, alpha=0.5)
nx.draw_networkx_nodes(G, pos, nodelist=partner_nodes, node_color=PACIFIC,
                       node_size=[_qsize(n) for n in partner_nodes], alpha=0.6, linewidths=0)
nx.draw_networkx_nodes(G, pos, nodelist=panel_nodes, node_color=RUST,
                       node_size=[_psize(n) for n in panel_nodes], edgecolors=INK, linewidths=0.6)
# Label panel genes; scale the label with betweenness so the big bridges read first.
_lbl = nx.draw_networkx_labels(G, pos, labels={n: n for n in panel_nodes},
                               font_color=INK, font_family="monospace", font_weight="bold", font_size=8)
for n, t in _lbl.items():
    t.set_fontsize(8 + (bc[n] / maxbc) ** 0.5 * 9)
    t.set_bbox(dict(facecolor=PAPER, edgecolor="none", alpha=0.7, pad=0.4))
plt.axis("off")
plt.title(f"ALS knowledge graph (ForceAtlas2, nodes sized by betweenness): {len(panel_nodes)} panel genes, "
          f"{len(partner_nodes)} STRING partners, {G.number_of_edges()} interactions",
          color=INK, fontsize=13, family="monospace")
plt.tight_layout()
plt.savefig(OUT, facecolor=PAPER, bbox_inches="tight")
print(f"wrote {OUT}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, {len(panel_nodes)} panel")
