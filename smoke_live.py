"""One-gene live smoke test of the repaired clients + populate normalizers.
Uses a throwaway temp cache (forces live fetch) and an in-memory DuckDB. Prints real values.
NOT part of the pipeline; run manually to validate before the full ingestion.
"""
import sys, tempfile, duckdb
from src.ingest.cache import DiskCache
from src.ingest.client import (UniProtClient, ReactomeClient, OpenTargetsClient,
                               ClinVarClient, StringClient, PubMedClient)
from src.db.schema import create_tables
from src.db import populate as P

GENE = sys.argv[1] if len(sys.argv) > 1 else "SOD1"
cache = DiskCache(tempfile.mkdtemp(prefix="smoke_cache_"), offline_mode=False)
conn = duckdb.connect(":memory:"); create_tables(conn)

up = UniProtClient(cache); rx = ReactomeClient(cache); ot = OpenTargetsClient(cache)
cv = ClinVarClient(cache); st = StringClient(cache, 0.7, 10); pm = PubMedClient(cache)

print(f"=== {GENE} ===")
u = up.get_gene_details(GENE)
acc = u.get("primaryAccession")
print("UniProt acc:", acc, "| gene:", (u.get("genes") or [{}])[0].get("geneName", {}).get("value"))
P.populate_uniprot(conn, {"results": [u]})

rxd = rx.get_pathways_for_uniprot(acc)
print("Reactome pathways:", len(rxd), "| sample:", [(p.get("stId"), p.get("displayName")) for p in rxd[:2]])
P.populate_reactome(conn, GENE, rxd)

otd = ot.fetch_gene_data(GENE)
assoc = otd.get("association") or {}
rows = assoc.get("associatedDiseases", {}).get("rows", [])
als = [(r["disease"]["id"], round(r["score"], 3)) for r in rows
       if "amyotrophic lateral sclerosis" in r["disease"]["name"].lower()]
print("OT ensembl:", otd.get("ensembl_id"), "| ALS assoc:", als[:3], "| drug rows:", len(otd.get("drugs", [])))
P.populate_opentargets(conn, otd)

cvd = cv.get_variants(GENE)
vs = cvd.get("variants") or {}
print("ClinVar variants:", len([k for k in vs if k != "uids"]))
P.populate_clinvar(conn, GENE, {"result": vs})

std = st.get_interactions(GENE)
print("STRING partners:", len(std), "| sample:", [(i.get("preferredName_B"), i.get("score")) for i in std[:2]])
P.populate_string(conn, std)

pmids = pm.search_pubmed(GENE, limit=10)
print("PubMed ids:", len(pmids), pmids[:5])

print("--- in-memory DB counts ---")
for t in ["genes", "pathways", "gene_pathways", "disease_associations", "drugs", "gene_drugs", "variants", "interactions"]:
    print(f"  {t}:", conn.execute(f"select count(*) from {t}").fetchone()[0])
print("  drugs sample:", conn.execute("select drug_id,name,max_clinical_phase from drugs limit 5").fetchall())
print("  variant sample:", conn.execute("select variant_id,clinical_significance from variants limit 3").fetchall())
print("  disease_assoc:", conn.execute("select gene_symbol,disease_id,round(score,3) from disease_associations").fetchall())
