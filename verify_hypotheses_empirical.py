import os
import sys
import re

def verify_hypotheses_format(file_path):
    print(f"=== Step 1: Checking existence of {file_path} ===")
    if not os.path.exists(file_path):
        print(f"ERROR: Hypotheses file not found at {file_path}")
        sys.exit(1)
    print("SUCCESS: File exists.")

    print("\n=== Step 2: Reading file content ===")
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by hypothesis header
    # Headers look like: ## HYP-001: ...
    # We want to find all matching blocks
    blocks = re.split(r'\n## (HYP-\d+):', content)
    
    # The first element is the text before the first hypothesis (e.g. # Generated Hypotheses\n\n)
    header_info = blocks[0]
    hyp_blocks = []
    
    for i in range(1, len(blocks), 2):
        hyp_id = blocks[i]
        hyp_content = blocks[i+1] if i+1 < len(blocks) else ""
        hyp_blocks.append((hyp_id, hyp_content))

    print(f"Total hypotheses parsed: {len(hyp_blocks)}")
    
    if len(hyp_blocks) == 0:
        # Check if there is an explanation for zero hypotheses
        if "No hypotheses generated" in content or "zero hypotheses" in content or "no connected pathways" in content:
            print("SUCCESS: Zero hypotheses generated, but a valid explanation is present.")
            sys.exit(0)
        else:
            print("ERROR: Zero hypotheses generated and no explanation found.")
            sys.exit(1)

    if len(hyp_blocks) < 3:
        print(f"WARNING/ERROR: Expected at least 3 hypotheses, but found only {len(hyp_blocks)}")
        sys.exit(1)
    else:
        print("SUCCESS: Found 3 or more hypotheses.")

    # 13 required sections in order:
    # 1. Title (header)
    # 2. Mechanism
    # 3. Genes involved
    # 4. Pathways involved
    # 5. Why this might matter in ALS
    # 6. Supporting evidence
    # 7. Contradicting or weak evidence
    # 8. Falsifiable prediction
    # 9. Computational validation
    # 10. High-level wet-lab concept
    # 11. Confidence
    # 12. Uncertainty
    # 13. Sources
    
    required_sections = [
        ("Mechanism", r'- \*\*Mechanism:\*\*'),
        ("Genes involved", r'- \*\*Genes involved:\*\*'),
        ("Pathways involved", r'- \*\*Pathways involved:\*\*'),
        ("Why this might matter in ALS", r'- \*\*Why this might matter in ALS:\*\*'),
        ("Supporting evidence", r'- \*\*Supporting evidence:\*\*'),
        ("Contradicting or weak evidence", r'- \*\*Contradicting or weak evidence:\*\*'),
        ("Falsifiable prediction", r'- \*\*Falsifiable prediction:\*\*'),
        ("Computational validation", r'- \*\*Computational validation:\*\*'),
        ("High-level wet-lab concept", r'- \*\*High-level wet-lab concept:\*\*'),
        ("Confidence", r'- \*\*Confidence:\*\*'),
        ("Uncertainty", r'- \*\*Uncertainty:\*\*'),
        ("Sources", r'- \*\*Sources\*\*:')
    ]

    all_valid = True
    for hyp_id, hyp_text in hyp_blocks:
        print(f"\nChecking format for {hyp_id}...")
        
        # We need to ensure each section matches in order
        last_index = 0
        hyp_valid = True
        
        for sec_name, sec_regex in required_sections:
            match = re.search(sec_regex, hyp_text)
            if not match:
                print(f"  ERROR: Missing section '{sec_name}'")
                hyp_valid = False
                all_valid = False
                continue
                
            start_pos = match.start()
            if start_pos < last_index:
                print(f"  ERROR: Section '{sec_name}' out of order (found at position {start_pos}, expected after {last_index})")
                hyp_valid = False
                all_valid = False
            else:
                last_index = match.end()
        
        # Check Sources content to make sure it contains valid PMIDs/DOIs
        sources_match = re.search(r'- \*\*Sources\*\*:\s*([\s\S]*?)(?=\n- \*\*|$)', hyp_text)
        if sources_match:
            sources_content = sources_match.group(1).strip()
            pmids = re.findall(r'PMID:\s*(\S+)', sources_content)
            if not pmids:
                print(f"  ERROR: No PMIDs found in Sources section: '{sources_content}'")
                hyp_valid = False
                all_valid = False
            else:
                # Check that no pmid is "not_found" or empty
                for p in pmids:
                    if p.lower() in ("not_found", "empty", "null", "none"):
                        print(f"  ERROR: Invalid source PMID: '{p}'")
                        hyp_valid = False
                        all_valid = False
        else:
            print("  ERROR: Could not parse content of Sources section")
            hyp_valid = False
            all_valid = False

        if hyp_valid:
            print(f"  SUCCESS: {hyp_id} format and citations are valid.")

    if all_valid:
        print("\nSUCCESS: All hypotheses are fully valid and conform to the 13-section layout with citations.")
        sys.exit(0)
    else:
        print("\nFAILURE: One or more hypotheses have invalid formatting or citation issues.")
        sys.exit(1)

if __name__ == "__main__":
    verify_hypotheses_format("outputs/hypotheses.md")
