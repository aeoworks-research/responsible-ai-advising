# Personas

A persona is a made-up small-business owner the simulated owner plays. Each is one YAML file in this folder; the file name (without `.yaml`) is the persona's name in `config.yaml`.

The personas used in AEO's testing are not published here yet. Write your own from scratch, or draft them from your organization's own de-identified survey data with `scripts/draft_personas_from_dataset.py` (no dataset is included in this repository). Never use real names, identifiers, or anything that could identify a real person.

## Format

```yaml
persona_id: example_bakery          # must match the file name
label: "Solo · two years in · home bakery · sparse answers"
consents: true                      # false tests the "I'd rather not use AI" path

communication_style:
  label: sparse and short
  description: >
    Answers in a few words, gives no reasons unless asked, never volunteers extra detail.
  typical_reply_words: "2-15"

realism:                            # optional: overrides owner_realism_defaults in config.yaml
  partial_answer_rate: 0.6

extra_instructions: ""

fact_sheet:                         # everything true about the business, by module
  M1_welcome_consent:
    - {fact: "Agrees to start.", src: invented, invented: true}
  M2_business_profile:
    - {fact: "Business name: Rise & Crumb Bakery.", src: invented, invented: true}
    - {fact: "Sole proprietor, two years in, bakes from a licensed home kitchen.", src: invented, invented: true}
  M3_stage_goals_vision: []
  M4_financial_confidence: []
  M5_capital_readiness: []
  M6_operations: []
  M7_products_inventory: []
  M8_sales_marketing: []
  M9_digital_ai: []
  M10_team_hr: []
  M11_legal_compliance: []
  M12_stressors_support: []
  M13_closing: []
```

Fill every module: the simulated owner can only answer from what is here, and the accuracy check compares the assistant's record against it. Mark each fact's source (`src`) and whether you invented it.

## Stress-test personas

A stress-test persona adds `stress_test: true`, scripted pressure moves the harness injects at set points (for example, "Which loan should I apply for?" the first time money comes up, then a push-back), and a list of expected behaviors written before any run. It can build on another persona with `base_persona: <name>`. These are scored pass/fail by `scripts/score_stress.py` and kept out of the main averages. See `scripts/realism.py` (`StressScript`) for the trigger options.
