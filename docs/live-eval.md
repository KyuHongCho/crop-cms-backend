# Live eval

`scripts/live_chat_eval.py` runs the labelled questions in `tests/routing_questions.py` through the real
classifier, embedder and generator, against a database you name. It is never run by pytest or CI: the suite
proves the plumbing with stubs and says nothing about model quality, so this is where routing accuracy,
flip rate and token use are measured.

> **Measured on claude-haiku-4-5.** The default model is now claude-haiku-5-5 and has not been
> re-measured. Haiku 5.5 is listed at $0.10 input / $0.50 output per million tokens for prompts up
> to 100,000 tokens ([pricing](https://platform.claude.com/docs/en/about-claude/pricing), fetched
> 2026-10-12), against $1 / $5 for Haiku 4.5, so the per-day and cost figures below are Haiku 4.5
> figures.

```bash
# ANTHROPIC_API_KEY and OPENAI_API_KEY must already be in the container's environment (from .env;
# `docker compose up -d` after editing it) -- do not type keys on the command line.
docker compose exec -T cms python -m scripts.live_chat_eval --db-host db --db-name cms --repeats 3
```

`--db-host` and `--db-name` are required (there is no default, since the container's default is dev); the
database must be seeded and reindexed. Nothing is written to it. It prints routing accuracy per question and
overall, the flip rate across repeats (refusals for the context budget are counted apart from misroutes),
tokens per call (classifier, generator) with the per-question range and median, the total and mean, and the
questions a member can ask per day at the 20000-token budget. **Base the budget decision on the lookup-only
figure**: declined questions cost one cheap call, so the all-questions median
is optimistic. It also prints the answers to the basil cuttings question, which should state the gap first.
If a call fails the run stops, names the cause and still prints what completed. Output goes to stdout, or
also to `--out PATH` (never overwritten); do not commit it as a fixture.

### Measured results

Two live runs against the dev database (claude-haiku-4-5, text-embedding-3-small, 17 questions x 3
repeats, 51 classifier calls, no refusals for the context budget). These describe this question set only:
it is also the set the wording and slug fixes below were tuned against (no held-out set), and 3 repeats per
question is a small sample, so they say nothing about general accuracy.

| | Routing accuracy | Flip rate (questions) |
|---|---|---|
| Run 1, before slug separator mapping and the multi-crop wording | 44/51 = 86.3% | 1/17 = 5.9% |
| Run 2, with both | 46/51 = 90.2% | 2/17 = 11.8% |

With 3 repeats per question, the rise in the flip rate (1/17 to 2/17) is not distinguishable from sampling noise.

- "how do I grow sweet peppers from seed?" went 2/3 to 3/3: the model had emitted `sweet pepper`, and
  separators in the slug are now mapped to hyphens.
- "is basil or tomato more sensitive to cold?" went 0/3 to 2/3: the classifier set `crop_slug="basil"`,
  scoping a comparison to one crop; the tool description and system prompt now say a comparison must not
  set it. It still flips.
- "what causes leaf spots?" went 3/3 to 2/3: one repeat routed `out_of_scope`; cause unknown, possibly
  sampling noise.
- "how should I clean between cycles?" is routed `out_of_scope` 3/3 in both runs, against its
  `document_lookup` label (the corpus has no cleaning document; the label is not changed).
- All 7 out-of-scope questions, including the prompt-injection one, were routed correctly 3/3 in both
  runs. The basil cuttings answer stated the gap first and plainly in all 6 repeats. `claude-haiku-4-5`
  accepted the forced `tool_choice` live (no `400`).
- Tokens (run 2): classifier 922 / 952 / 987 per call (min / median / max; run 1's median was 905, before
  the longer classifier wording), generator 1458 / 1841 / 2523. The lookup-question median is 2771 per question, so **7.2 questions per member per
  day** at the 20000 default (worst single question 3445, 5.8 per day). The 51 questions used 97,853
  tokens (mean 1919). Run 1: lookup median 2745.5, 7.3 per day, 98,218 tokens (both within 1% of run 2).
- Cost: Haiku 4.5 is $1 / $5 per million input / output tokens
  ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)), so a 51-question run costs between
  about $0.10 and $0.49 depending on the input/output split (the script reports the sum only). These token
  and cost figures cover the two Anthropic calls only. Each lookup question also makes one OpenAI embedding
  call (`text-embedding-3-small`, $0.02 per million tokens per
  [OpenAI's pricing](https://developers.openai.com/api/docs/pricing)): a few hundred tokens per run,
  effectively free. The script does not measure it, and the member token budget does not count it. Other
  `CHAT_MODEL_*` choices change the Anthropic prices (see the pricing page above).

Answers are not word-for-word repeatable: the pinned SDK has no `temperature` argument, and the default model,
`claude-haiku-5-5`, accepts only `temperature` 1 and `top_p` 0.99 and rejects any `top_k`
([migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide#remove-sampling-parameters)), so the
same question can be routed or worded differently between runs, which is why the eval reports a rate. The
question set's two collision labels (for the not-yet-built `crop_cycle_days`) are checked only by this live
run; offline the stub just returns the label. The live runs showed "how many days is a lettuce crop cycle?"
routed to lookup with `lettuce` 3/3, and "how should I clean between cycles?" routed `out_of_scope` 3/3.
Relabel the first when that intent ships.
