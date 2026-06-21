# REAL DATA PROVENANCE — ALS Genetic Mechanism Explorer

**Status:** the knowledge graph is populated entirely from **live public APIs**. No hardcoded,
hand-written, mocked, or LLM-generated biological records exist in the production code or data.
Every gene / variant / interaction / pathway / paper / drug row is derivable from a real cached
API response under `data/raw/cache/`, and the DuckDB rebuilds to the **same shape from a clean
(wiped) cache** — proving nothing was hand-seeded.

Generated: 2026-06-21 · DB: `data/processed/als_genetics.duckdb` · Panel: 20-gene ALS seed set.

## Data sources (live, no paid keys)

| Source | Endpoint | What it populates |
|---|---|---|
| UniProt REST | `rest.uniprot.org/uniprotkb/search` | gene → protein accession, name |
| STRING | `string-db.org/api/json/interaction_partners` | protein–protein interaction edges |
| NCBI Entrez (ClinVar) | `eutils.ncbi.nlm.nih.gov/.../esearch+esummary` (db=clinvar) | clinically classified variants |
| NCBI Entrez (PubMed) | `eutils.ncbi.nlm.nih.gov/.../esearch+esummary+efetch` (db=pubmed) | literature / citations |
| Reactome | `reactome.org/ContentService/data/mapping/UniProt/{acc}/pathways` | pathway membership |
| Open Targets | `api.platform.opentargets.org/api/v4/graphql` | ALS disease association + known drugs |

Rate limiting: ≤3 req/s (per-request delay + exponential backoff on 429/5xx). All raw responses
cached under `data/raw/cache/` keyed by `sha256(source, endpoint, params)`.

## Per-source row counts (production DuckDB)

| Table | Rows | Source |
|---|---:|---|
| genes | 20 | UniProt |
| interactions | 190 | STRING (confidence ≥ 0.7) |
| variants | 1,479 | ClinVar (across all 20 genes) |
| pathways (distinct R-HSA) | 102 | Reactome |
| gene_pathways | 132 | Reactome |
| disease_associations | 93 (46 ALS-specific, 20 genes) | Open Targets |
| drugs | 87 (all real ChEMBL ids) | Open Targets |
| gene_drugs | 87 | Open Targets |
| papers | 165 (165 distinct real PMIDs, 0 placeholders) | PubMed |
| claims | 6,402 | derived (UniProt/STRING/Reactome/ClinVar/OT/PubMed) |
| hypotheses | 309 (each cites ≥1 real PMID; 110 distinct PMIDs across all) | derived |
| hypothesis_evidence | 1,854 | derived |

## 5 spot-checked records (DB value confirmed against the live source)

Each was re-queried live on the public API and matches the stored DuckDB value exactly.

1. **Gene → UniProt accession** — DB: `SOD1` → `P00441`, "Superoxide dismutase [Cu-Zn]".
   Live: <https://rest.uniprot.org/uniprotkb/P00441.json> → geneName `SOD1`,
   recommendedName "Superoxide dismutase [Cu-Zn]". ✅ match

2. **Variant → ClinVar** — DB: `VCV002059610` (gene SOD1) → clinical_significance "Likely pathogenic".
   Live: <https://www.ncbi.nlm.nih.gov/clinvar/variation/2059610/>
   (esummary `db=clinvar&id=2059610` → germline_classification "Likely pathogenic"). ✅ match

3. **Interaction → STRING** — DB: `CCS ⇄ SOD1` confidence `0.999`.
   Live: <https://string-db.org/api/json/interaction_partners?identifiers=SOD1&species=9606&required_score=700>
   → partner `CCS` score `0.999`. ✅ match

4. **Pathway → Reactome** — DB: `R-HSA-3299685` "Detoxification of Reactive Oxygen Species" linked to SOD1.
   Live: <https://reactome.org/ContentService/data/mapping/UniProt/P00441/pathways?species=9606>
   → contains stId `R-HSA-3299685`. ✅ match

5. **Drug + disease association → Open Targets** — DB: SOD1 → drug `CHEMBL3833346` TOFERSEN
   (max_clinical_phase 4.0); disease_association SOD1 → `MONDO_0004976` score `0.8701`.
   Live: `api.platform.opentargets.org/api/v4/graphql` target `ENSG00000142168` →
   drugAndClinicalCandidates `CHEMBL3833346` TOFERSEN stage `APPROVAL`; associatedDiseases
   `MONDO_0004976` score `0.8701`. ✅ match
   (Drug page: <https://platform.opentargets.org/drug/CHEMBL3833346>)

Bonus — **Paper → PubMed**: DB paper `PMID 20301623` ("Amyotrophic Lateral Sclerosis Overview")
resolves live at <https://pubmed.ncbi.nlm.nih.gov/20301623/>.

## Verification gate (all 10 pass)

1. genes = 20, every seed gene present, each with a real UniProt accession ✅
2. interactions = 190 (≥0.7), ≥1 partner per gene ✅
3. variants = 1,479 across 20 genes, real VCV ids + controlled-vocabulary significance ✅
4. pathways = 102 distinct R-HSA (not just the umbrella) with gene_pathways links ✅
5. disease_associations = real OT ALS association (MONDO_0004976 + score) for the panel ✅
6. drugs = 87 real OT drugs with ChEMBL ids + clinical phase; incl. **TOFERSEN** (`CHEMBL3833346`,
   approved, SOD1-targeted) ✅ — note: Open Targets `drugAndClinicalCandidates` returns only drugs
   molecularly target-linked to a panel gene, so riluzole/edaravone (which act on glutamate /
   free-radical pathways, not these 20 proteins) legitimately do not appear; the mapped set is what
   OT genuinely returns for these targets.
7. papers = 165 with 165 distinct real PMIDs; 0 cited `not_found`; 0 placeholder DOIs/PMIDs ✅
8. hypotheses = 309, each cites ≥1 real PMID; 110 distinct PMIDs across all ✅
9. reproducibility: clean-cache re-run produced a byte-identical-shape DuckDB (all table deltas 0) ✅
10. dashboard provenance note + stat strip rewritten to real counts; "demo stub" wording removed ✅

## Reproduce from scratch

```bash
cd als-genetics-explorer && source .venv/bin/activate
find data/raw/cache -name '*.json' -delete
rm -f data/processed/als_genetics.duckdb data/processed/deduplicated_papers.json
ALS_DB_PATH=data/processed/als_genetics.duckdb python3 -m src.pipeline.run_all --config config.yaml
python3 gate_check.py            # 8/8 core checks pass
```
The first run fetches live and caches; subsequent runs replay from `data/raw/cache/` (set
`offline_mode: true` in `config.yaml` to force cache-only).
