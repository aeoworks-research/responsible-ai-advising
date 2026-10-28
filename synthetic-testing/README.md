# Synthetic testing

Tools for testing an AI intake prompt, such as the Business Health Assessment prompt in `../prompt/`, against **simulated** small-business owners before anyone real uses it. What we found with them is in [RESULTS.md](RESULTS.md).

## What it is

An AI plays a business owner (a "persona" with a fixed fact sheet), and another AI runs your intake prompt with them. Because the owner's facts are known, you can check what the intake asked, what it recorded, and whether it stayed within its guardrails. You can run the same owner through two prompt versions and compare them, many times over.

## What it can and can't show

**It can show:**

- whether the intake stays within guardrails (no legal, tax, financial, lending or HR-compliance advice; no product recommendations; consent; a non-AI exit), including when the owner pushes for advice;
- how long the conversation is and how much the owner has to type and read;
- which fields the structured record captures, and whether recorded values match what the owner said;
- how a prompt behaves with different kinds of owners (talkative, terse, skeptical of AI, tired).

**It can't show:**

- whether real entrepreneurs would finish the conversation or trust it;
- whether advisors save time or make better decisions;
- whether one advisor summary is genuinely more useful than another, unless people check the grader's judgments (see the caution below).

## How a run works

1. **Simulated owner:** plays one persona. It sees only the persona's facts and speaking style, never your prompt. Each turn, the testing harness may tell it to answer only part of a question, say "not sure", prefer not to say, or keep it short as it tires, using the same rules for every prompt version.
2. **Intake assistant:** runs your prompt version, from consent to the final advisor handoff and structured record.
3. **Record extractor:** copies the assistant's structured record into a fixed field list. It sees only the handoff, never the conversation, so it can't fill gaps.
4. **Accuracy checker:** compares each recorded value with the persona's fact sheet.
5. **Grader:** scores the conversation and the handoff on the rubric in `rubric/` without being told which prompt version produced them, and flags guardrail problems with quotes. Stress-test personas are scored pass/fail on behaviors written before the run.

Every run saves the full transcript, the handoff, and a record of models, settings and costs (`runs/`), so results can be traced and repeated.

## Setup

1. Install Python 3.11.
2. In this folder, create a virtual environment and install the requirements:
   `python -m venv .venv`, then `.venv/Scripts/pip install -r requirements.txt` (Windows) or `.venv/bin/pip install -r requirements.txt` (Mac/Linux).
3. Get an Anthropic API key and set it as the environment variable `ANTHROPIC_API_KEY`. The scripts read the key only from there; never put it in a file.
4. Copy `config.example.yaml` to `config.yaml` and set a spending cap (`budget_usd`).
5. Run `python scripts/extract_prompts.py` to prepare the prompt versions listed in `prompts/registry.yaml`.

Then, for a grid of prompts × personas defined in `config.yaml`:

```
python scripts/run_intake.py --grid example_grid     # conversations
python scripts/extract_record.py                     # structured records
python scripts/score_run.py                          # rubric scores
python scripts/compare_handoffs.py                   # side-by-side handoff comparisons
python scripts/score_stress.py                       # stress-test pass/fail
python scripts/check_demographics.py                 # personal details in handoffs
python scripts/summarize.py                          # report in reports/summary.md
```

## Cost

About **45 cents per conversation** with the default models; automated scoring (record extraction and rubric grading) roughly doubles that. Every script records its spending and stops at your `budget_usd`. Check current model prices before you start.

## Using your own prompt and personas

- **Prompt:** save it as a text file, add an entry to `prompts/registry.yaml`, and run `python scripts/extract_prompts.py`. If your prompt ends with a structured record template like ours ("STRUCTURED INTAKE RECORD TEMPLATE", then `Category: field; field; ...` lines), the scripts read its field list automatically.
- **Personas:** see [personas/README.md](personas/README.md) for the format. AEO's own personas aren't included yet. `scripts/draft_personas_from_dataset.py` can draft personas from your organization's de-identified survey data with a matching data dictionary. **No dataset is included**, and you should never use data that still contains names, TIN/EIN/SSN or other identifiers.
- **Rubric and instructions:** the grader's rubric is in `rubric/`, and every AI role's instructions are plain text in `templates/`, so you can read and change them.

## Caution: check the grader against people before trusting it

An AI grader is a fast, consistent stand-in for human judgment, but it isn't human judgment.

- **Our first grader was too generous:** it gave top marks to nearly everything and didn't rank summaries the way our human reviewer did.
- **We recalibrated it:** we added scoring anchors (`rubric/rubric.md`; the original is kept as `rubric_v1_2026-10-24.md`). Its score levels came closer to the reviewer's, but its preferences between summaries still haven't been confirmed by human reviewers.

Before relying on grader scores, have one or two experienced advisors judge a sample (`scripts/export_for_human_scoring.py` exports one with prompt labels hidden) and compare. Counted results such as conversation length, fields captured and guardrail checks are more dependable than quality ratings.

## Files

- `scripts/`: the Python scripts above, plus shared code (`common.py`) and the realistic-owner logic (`realism.py`).
- `templates/`: instructions for each AI role. Earlier versions are kept (`*_v1_*`, `*_experiment`) to document how the grader was recalibrated.
- `rubric/`: the scoring rubric (version 2) and the original.
- `prompts/`: the prompt registry and the prepared prompt files and field lists.
- `personas/`, `sample-runs/`: formats and examples (AEO's personas and sample runs will be added later).
