# Responsible AI Advising

The Association for Enterprise Opportunity's (AEO) tools for responsible AI-enabled business advising, starting with the **AI-enabled Business Health Assessment (BHA)**: a prompt that has an AI assistant run AEO's Business Health Assessment as a short conversation with a small-business owner, then prepare a summary and a structured record for the owner's advisor.

The insight brief [*Getting AI Right in Business Advising*](https://research.aeoworks.org/publications/responsible-ai-advising) explains what we learned and why it matters.

## Who it's for

Entrepreneur support organizations, business advisors and coaches, and anyone helping small businesses who wants to use AI to prepare for advising while keeping people in charge.

## What's here

| Folder | What it is |
|---|---|
| [`prompt/`](prompt/) | The current consolidated master prompt, its structured intake record template, a changelog of how it was developed with practitioners, and the earlier versions we tested. |
| [`synthetic-testing/`](synthetic-testing/) | Tools to test an intake prompt against simulated business owners, with a rubric, an AI grader and guardrail stress tests, plus our [results](synthetic-testing/RESULTS.md). |
| [`brief/`](brief/) | A pointer to the insight brief. |
| [`toolkit/`](toolkit/) | The Responsible AI Advising Toolkit (coming soon). |
| [`docs/`](docs/) | The project's landing page, published with GitHub Pages (link to be added). |

## Where to start

- **Advisors and program staff:** read the [brief](brief/), then open [`prompt/consolidated_master_prompt.txt`](prompt/consolidated_master_prompt.txt) and try it with a made-up business.
- **People who want to adapt or test the prompt:** see [`prompt/CHANGELOG.md`](prompt/CHANGELOG.md) and [`synthetic-testing/`](synthetic-testing/).

## Three ways to use it

1. **No code:** paste the prompt into a general-purpose AI assistant, using a plan that does not train on your data. An advisor reviews the output before it is used.
2. **Self-hosted app:** coming.
3. **CRM connection:** coming.

## Core guardrails

- **No advice:** the assistant gives no legal, tax, financial, lending or HR-compliance advice. It names the topic and suggests the kind of professional to consult.
- **No product steering or promises:** no recommended lenders, loans, grants, financial products or AI vendors; no judgments of creditworthiness; no promised time savings or results.
- **Advisor review:** an advisor reviews everything the AI produces before it is used or shared with the owner.
- **Protect identifiers:** never enter a TIN, EIN or Social Security number.
- **Consent and a non-AI path:** the conversation starts with consent, anyone can stop at any time, and their advisor gathers the information another way.

## Limits

- The prompt has been tested with **simulated** business owners and with practitioners role-playing test cases, not yet with real entrepreneurs. We don't yet know whether real owners would finish the conversation or trust it, or whether advisors save time.
- Our AI grader's judgments of summary quality haven't been confirmed by human reviewers; counted results (length, fields captured, guardrail checks) are more dependable.
- Testing used one AI model family. Results can change when models change, so re-test after updates.
- The current version's revenue question uses AEO's standard ranges and hasn't been re-tested since the change (see the changelog).

## License

Code (`synthetic-testing/scripts/`) is under the [MIT License](LICENSE). Prompts, documentation, rubric, templates and other written content are under [CC BY 4.0](LICENSE-docs).

## Credits

Developed by the Association for Enterprise Opportunity (AEO), with support from PayPal.

AEO thanks the practitioners and organizations who tested, questioned, and improved the
assessment and shaped this work:

- Lashawna Manigault, Director, District Business Liaison Program, Detroit Economic Growth Corporation (DEGC)
- Brandon McDonald, Director of Strategic Partnerships and Innovation, Northern Maine Development Commission (NMDC)
- Sherina McKinley, Former Co-CEO, Uptima Entrepreneur Cooperative
- Melissa Nong, Director, Digital and Community Engagement, The Acceleration Project (TAP)
- Kevin Wilson, Executive Director, St. Louis Small Business Empowerment Center (SBEC)

Their experience and candor made this work possible. The views here are AEO's and do not
necessarily reflect those of participating organizations.

Maintained by AEO Research (research@aeoworks.org).

## How to cite

Association for Enterprise Opportunity. (2026). *Responsible AI Advising Toolkit and
AI-Enabled Business Health Assessment* [Repository]. [Link to be added]
