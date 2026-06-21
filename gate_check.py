"""Verification gate for the ALS Explorer real-data pipeline. Read-only DB queries.
Prints PASS/FAIL per check with the evidence numbers."""
import duckdb, yaml, sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/processed/als_genetics.duckdb"
c = duckdb.connect(db, read_only=True)
seed = yaml.safe_load(open("config.yaml"))["seed_genes"]
thr = 0.7
results = []
def chk(n, ok, msg): results.append((n, ok, msg)); print(f"[{'PASS' if ok else 'FAIL'}] {n}. {msg}")

# 1 genes
ngenes = c.execute("select count(*) from genes").fetchone()[0]
present = set(r[0] for r in c.execute("select gene_symbol from genes").fetchall())
missing = [g for g in seed if g not in present]
no_acc = c.execute("select count(*) from genes where uniprot_id is null or uniprot_id=''").fetchone()[0]
chk(1, ngenes >= 20 and not missing and no_acc == 0,
    f"genes={ngenes}, seed missing={missing}, rows w/o uniprot_acc={no_acc}")

# 2 interactions
nint = c.execute("select count(*) from interactions").fetchone()[0]
below = c.execute("select count(*) from interactions where confidence_score < ?", [thr]).fetchone()[0]
genes_with_partner = c.execute(
    "select count(distinct g) from (select gene_a g from interactions union select gene_b from interactions)").fetchone()[0]
seed_no_partner = [g for g in seed if c.execute(
    "select count(*) from interactions where gene_a=? or gene_b=?", [g, g]).fetchone()[0] == 0]
chk(2, nint >= 100 and below == 0 and not seed_no_partner,
    f"interactions={nint}, below_thr={below}, seed w/o partner={seed_no_partner}")

# 3 variants
nvar = c.execute("select count(*) from variants").fetchone()[0]
vgenes = c.execute("select count(distinct gene_symbol) from variants").fetchone()[0]
bad_id = c.execute("select count(*) from variants where variant_id is null or variant_id=''").fetchone()[0]
no_sig = c.execute("select count(*) from variants where clinical_significance is null or clinical_significance=''").fetchone()[0]
sigs = [r[0] for r in c.execute("select distinct clinical_significance from variants").fetchall()]
chk(3, nvar >= 50 and vgenes >= 8 and bad_id == 0,
    f"variants={nvar}, distinct_genes={vgenes}, bad_id={bad_id}, no_sig={no_sig}, sig_vocab={sigs[:6]}")

# 4 pathways
npw = c.execute("select count(distinct pathway_id) from pathways where pathway_id like 'R-HSA-%'").fetchone()[0]
gplinks = c.execute("select count(*) from gene_pathways").fetchone()[0]
distinct_nonumbrella = c.execute("select count(distinct pathway_id) from pathways where pathway_id like 'R-HSA-%' and pathway_id <> 'R-HSA-9711123'").fetchone()[0]
chk(4, npw >= 15 and gplinks > 0 and distinct_nonumbrella >= 15,
    f"distinct R-HSA pathways={npw}, gene_pathway_links={gplinks}")

# 5 disease associations (OT ALS)
als_ids = ('EFO_0000253','MONDO_0004976','EFO_0001356','EFO_0001357')
nda = c.execute("select count(*) from disease_associations").fetchone()[0]
als_da = c.execute(f"select count(*) from disease_associations where disease_id in {als_ids}").fetchone()[0]
da_genes = c.execute(f"select count(distinct gene_symbol) from disease_associations where disease_id in {als_ids}").fetchone()[0]
bad_score = c.execute("select count(*) from disease_associations where score is null").fetchone()[0]
chk(5, als_da >= 1 and da_genes >= 1 and bad_score == 0,
    f"disease_assoc rows={nda}, ALS rows={als_da} across {da_genes} genes, null_score={bad_score}")

# 6 drugs
ndr = c.execute("select count(*) from drugs").fetchone()[0]
ngd = c.execute("select count(*) from gene_drugs").fetchone()[0]
chembl = c.execute("select count(*) from drugs where drug_id like 'CHEMBL%'").fetchone()[0]
phased = c.execute("select count(*) from drugs where max_clinical_phase is not null").fetchone()[0]
names = [r[0] for r in c.execute("select name from drugs order by max_clinical_phase desc nulls last limit 8").fetchall()]
chk(6, ndr >= 3 and ngd >= 3 and chembl == ndr and phased >= 3,
    f"drugs={ndr}, gene_drugs={ngd}, chembl_ids={chembl}, with_phase={phased}, names={names}")

# 7 papers
npapers = c.execute("select count(*) from papers where pmid <> 'not_found'").fetchone()[0]
distinct_pmid = c.execute("select count(distinct pmid) from papers where pmid <> 'not_found' and pmid ~ '^[0-9]+$'").fetchone()[0]
nf_paper = c.execute("select count(*) from papers where pmid='not_found'").fetchone()[0]
cited_nf = c.execute("select count(*) from claims where paper_id='not_found' and paper_id in (select pmid from papers)").fetchone()[0]
he_nf = c.execute("select count(*) from hypothesis_evidence where pmid='not_found'").fetchone()[0]
bad_doi = c.execute("select count(*) from papers where doi like '%nature123%'").fetchone()[0]
bad_pmid = c.execute("select count(*) from papers where pmid='31567890'").fetchone()[0]
chk(7, npapers >= 100 and distinct_pmid >= 50 and he_nf == 0 and bad_doi == 0 and bad_pmid == 0,
    f"papers={npapers}, distinct_real_pmid={distinct_pmid}, not_found_paper_rows={nf_paper}, "
    f"hyp_evidence_not_found={he_nf}, placeholder_doi={bad_doi}, placeholder_pmid={bad_pmid}")

# 8 hypotheses
nhyp = c.execute("select count(*) from hypotheses").fetchone()[0]
hyp_with_ev = c.execute("select count(distinct hypothesis_id) from hypothesis_evidence where pmid ~ '^[0-9]+$'").fetchone()[0]
distinct_hyp_pmid = c.execute("select count(distinct pmid) from hypothesis_evidence where pmid ~ '^[0-9]+$'").fetchone()[0]
chk(8, nhyp >= 1 and hyp_with_ev == nhyp and distinct_hyp_pmid >= 20,
    f"hypotheses={nhyp}, with>=1 real PMID={hyp_with_ev}, distinct PMIDs across hyps={distinct_hyp_pmid}")

print("\n=== SUMMARY ===")
npass = sum(1 for _,ok,_ in results if ok)
print(f"{npass}/8 core checks pass (9=reproducibility, 10=dashboard handled separately)")
sys.exit(0 if npass == 8 else 1)
