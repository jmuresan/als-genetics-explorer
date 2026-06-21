# Lead Hypothesis Brief — for external review

**Candidate seeded from:** `HYP-191` (the only non-tautological hypothesis in the generated set:
a direct CCS ⇄ SOD1 network-proximity edge, STRING confidence 0.999).
**Status:** analyst synthesis on top of the pipeline; citations below were each verified
individually against PubMed / journal pages (the pipeline's own single citation,
`PMID 31567890` / DOI `10.1038/nature123`, is a placeholder and should be ignored).

---

## Hypothesis (one sentence)

**The CCS-to-SOD1 stoichiometric ratio is the variable that determines whether copper
delivery to motor neurons is neuroprotective or neurotoxic — and differences in that ratio
between mouse models and human spinal cord may explain why CuATSM extended survival in
SOD1-G93A mice yet showed no benefit on motor-neuron pathology in the clinic.**

## Why it is plausible (a published paradox it resolves)

1. **Anchor.** CCS is the dedicated copper chaperone that metallates and matures Cu/Zn-SOD1.
   *(Wong et al., PNAS 2000; 97:2886 — https://www.pnas.org/doi/10.1073/pnas.040461197)*
2. **Toxic pole.** Over-supplying that pathway is catastrophic: CCS overexpression collapses
   SOD1-G93A mouse survival from ~242 to ~36 days and drives mutant SOD1 into mitochondria.
   *(Son et al., PNAS 2007 — PMID 17389365)*
3. **Protective pole.** Yet CuATSM, which delivers copper to the CNS, extends survival in the
   same SOD1-G93A model — but only within a narrow dose window (toxic when too high).
   *(Sci Rep 2021 — https://www.nature.com/articles/s41598-021-98317-w)*
4. **The clinical gap.** A post-mortem analysis of CuATSM-treated patients found no significant
   effect on motor-neuron density or TDP-43 burden vs. untreated — the preclinical promise did
   not translate. *(PMC10947464, 2024)*
5. **Supporting nuance.** CuATSM's benefit is metal-state dependent: it protects against
   WT-like SOD1 mutants but not mutants that disrupt metal binding.
   *(Roberts et al., 2018 — PMID 30462490)*. Copper distribution is also disrupted in sporadic
   ALS spinal cord. *(Sci Rep 2024 — https://www.nature.com/articles/s41598-024-55832-w)*

**Mechanistic claim.** Where CCS is *limiting* relative to (mutant) SOD1, supplemental copper
safely matures apo-SOD1 → net protective. Where CCS is in *excess*, the same copper is
over-delivered and routed to mitochondrial mutant SOD1 → net toxic. The CCS:SOD1 ratio (and
cytosolic-vs-mitochondrial copper routing) is therefore a candidate **response biomarker /
stratification variable** for copper-based ALS therapy.

## Why it is novel

The individual facts are published, but — to the best of this review — **no one has proposed
CCS:SOD1 stoichiometry as the switch reconciling the protective (CuATSM) and toxic
(CCS-overexpression) poles, nor as a stratification biomarker for the stalled CuATSM program.**
That synthesis is the contribution.

## Falsifiable predictions

- **P1.** Across an iPSC-derived motor-neuron panel spanning SOD1 genotypes, CuATSM benefit
  (survival / mitochondrial function) is **inversely proportional** to baseline CCS:SOD1
  protein ratio (western blot or targeted MS).
- **P2.** CRISPRi knockdown of CCS **converts** CuATSM from neutral/toxic to protective in
  high-CCS lines; CCS overexpression **abolishes** CuATSM benefit in low-CCS lines.
- **P3.** In CuATSM trial autopsy tissue, responders vs. non-responders differ in CCS:SOD1
  ratio and in mitochondrial copper / SOD1 localisation.

## Kill criterion

If CuATSM efficacy is **independent** of CCS:SOD1 ratio across the iPSC panel, the hypothesis
is falsified. One clean experiment ends it.

## What to flag to the reviewer

- This came out of a demo-stub knowledge graph (1 fully-ingested gene). The *only* thing the
  pipeline contributed was the CCS⇄SOD1 edge; everything else here is human/literature work.
- Worth a domain expert's gut check on: (a) whether CCS:SOD1 stoichiometry is already
  measurable in existing iPSC/autopsy resources, and (b) whether CuATSM trial biobank tissue
  is accessible for the P3 test.
