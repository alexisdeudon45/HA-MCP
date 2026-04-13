# Prompt: Analysis Phase

## Objective

Compare the structured candidate model against the structured job model to produce a comprehensive analysis.

## Required Outputs

### Alignments
For each job requirement, assess the candidate's matching evidence:
- `requirement`: What the job asks for
- `candidate_evidence`: What the candidate offers
- `alignment_score`: 0.0 (no match) to 1.0 (perfect match)
- `status`: aligned | partial | gap | exceeded

### Gaps
For each unmet or partially met requirement:
- `requirement`: The unmet requirement
- `severity`: critical | significant | minor
- `candidate_closest`: Nearest matching element
- `remediation_potential`: Can the gap be bridged? (high/medium/low/none)

### Signals
Broader observations beyond direct requirement matching:
- `type`: strength | risk | opportunity | neutral
- `description`: What was observed
- `evidence`: Supporting evidence
- `impact`: high | medium | low

### Uncertainties
Areas where data is incomplete or ambiguous:
- `area`: What is uncertain
- `reason`: Why it's uncertain
- `suggested_verification`: How to resolve

### Overall Assessment
- `overall_score`: Weighted composite score (0.0-1.0)
- `recommendation`: strong_match | good_match | partial_match | weak_match | no_match
- `priorities`: Ranked list of items to highlight, address, or verify
