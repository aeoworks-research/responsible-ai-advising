# Prompt changelog

How the AI-enabled Business Health Assessment prompt developed. Each change was made with a group of practitioners from entrepreneur support organizations (the cohort) who tested the prompt, scored the results, and recommended revisions. The tested versions are in `tested_versions/`. The current version is `consolidated_master_prompt.txt`.

## Original V0 prompt (June 2026)

AEO's first master intake prompt ran the assessment as a conversation in thirteen modules (M1 welcome and consent through M13 closing and advisor handoff). It produced a structured intake record and an advisor-facing summary, with guardrails against legal, tax, financial, lending and HR-compliance advice.

## First revision (Round 1, June–July 2026): `tested_versions/first_revision.txt`

- **What:** cohort testers revised the original prompt during their first testing round, focusing on how the advisor summary is laid out: a skimmable format with key facts, the owner's own words and the top priorities up front. This became the baseline ("control") for Round 2.
- **Why:** testers found the original output hard to scan before a session.
- **Note:** the tested copy includes the AI & technology assessment added on Sep 22 (see below). It was added to every Round 2 version as a shared update, so comparisons held it constant.

## Round 2 one-lever tests (July 2026)

After the Round 1 debrief, three variants each changed one part of the first revision, so the cohort could see what each change did on its own:

- **Answer types:** offer short lettered or numbered answer menus, with an "or tell me in your own words" option. *Why:* the cohort reported typing fatigue and long, compound questions.
- **Depth and rapport:** open with one or two warm questions ("what's the bigger why", "what keeps you up at night"), go one level deeper on goals and stressors, and ask about legacy and what success would feel like. *Why:* the cohort recommended building trust before collecting facts.
- **Advisor handoff / shorter intake:** break big questions into small ones, move most deep follow-ups into the advisor's suggested questions, frame the main goal as a three-month step, and ask what documents (plan, budget) the owner has and whether they want help with them. *Why:* the cohort recommended a shorter live conversation and goals sized to the advising window.

## Consolidation (August 5, 2026)

- **What:** the changes the cohort judged generalizable were merged into one prompt: the rapport opener with a graceful way to opt out of using AI; structured answer choices with a write-in option; a shorter intake with big questions broken up; one deeper follow-up where it matters, with other follow-ups moved to the advisor list; three-month goal framing plus legacy and success; and the documents-on-hand question. New record fields: `documents_on_hand`, `documents_last_updated`, `documents_support_needed`, `goal_time_horizon`, `legacy_or_impact_desired`.
- **Why:** to carry forward what worked across the Round 2 tests. Ideas specific to one organization's workflow were logged for later versions instead.

## AI & technology opportunities (September 22, 2026): `tested_versions/consolidated_sep22.txt`

- **What:** M9 now first asks what tools the owner uses and how confident they feel, then surfaces repetitive or time-consuming tasks where AI or a simple tool might help, the owner's comfort and interest, and their barriers (cost, time, trust, internet or device access). The handoff gained an "AI & technology opportunities" section that must always appear, even if only to say little surfaced. The record gained `current_ai_use`, `repetitive_or_time_consuming_tasks`, `ai_or_tech_opportunity_areas`, `ai_comfort_and_interest` and `ai_barriers_or_concerns`.
- **Guardrails added with it:** never tell the owner they must adopt AI; never recommend specific AI products, apps or vendors as the right choice; never promise time savings or results; never collect new personal or financial data to assess AI fit; anything about tool costs goes to the advisor.
- **Why:** the cohort wanted the assessment to help owners notice where AI could help, without selling it.

## Optional revenue question (October 2026): `consolidated_master_prompt.txt`

- **What:** M4 includes one optional question about last year's revenue, offered as a lettered menu, and the record gained `revenue_range_last_year`. The owner can answer "Not sure" or "Prefer not to say", and the assistant never presses for an exact figure or uses the answer to judge creditworthiness, viability or funding eligibility.
- **Why:** the original survey asked for revenue, and advisors need a rough sense of business size; the earlier conversational versions had no revenue question at all.
- **Tested vs. current wording:** the version tested in synthetic conversations (`tested_versions/consolidated_sep22_revenue_test.txt`) used seven broad ranges. The current version uses AEO's eleven standard revenue ranges, treats "I was not in business in the last calendar year", "Not sure" and "Prefer not to say" as distinct, recorded answers, and **has not been re-tested**.
