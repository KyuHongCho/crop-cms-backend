"""Manual live eval of POST /chat's routing and token use. NOT run by pytest or CI; it spends money.

    docker compose exec -T cms python -m scripts.live_chat_eval --db-host db --db-name cms [--repeats 3]

--db-host/--db-name are required (the default env is the dev DB); needs both API keys; writes nothing.
Accuracy is a rate: temperature cannot be set, so repeats can differ. For the budget use the LOOKUP-ONLY
figure: declined questions cost one cheap call and flatter the all-questions median.
--out is never overwritten; do not commit its output as a fixture.
"""
import argparse
import asyncio
import os
import statistics
import sys
from typing import NamedTuple

from tests.routing_questions import CUTTINGS_QUESTION, DOCUMENT_LOOKUP, QUESTIONS

DEFAULT_BUDGET = 20000


class Call(NamedTuple):
    routed: tuple  # (intent, crop_slug or None), even when refused
    refused: bool  # the context budget refused the request after routing
    classifier_tokens: int
    generator_tokens: int | None  # None: no generator call
    answer: str

    @property
    def tokens(self) -> int:
        return self.classifier_tokens + (self.generator_tokens or 0)


# --- the summary maths: pure, so tests/test_live_chat_eval.py can pin it ---

def want(question) -> tuple:
    return (question.intent, question.crop_slug if question.intent == DOCUMENT_LOOKUP else None)


def per_day(budget: int, tokens: float) -> float | None:
    """Questions per member per day, or None when no tokens were reported."""
    return budget / tokens if tokens > 0 else None


def fmt_per_day(value: float | None) -> str:
    return "n/a (no tokens reported)" if value is None else f"{value:.1f}"


def summarise(values: list) -> str:
    return f"min {min(values)} / median {statistics.median(values):g} / max {max(values)}" if values else "none"


def routing_stats(rows) -> dict:
    calls = [c for _q, cs in rows for c in cs]
    correct = sum(c.routed == want(q) for q, cs in rows for c in cs)
    flips = sum(len({c.routed for c in cs}) > 1 for _q, cs in rows)
    return {"calls": len(calls), "correct": correct, "refused": sum(c.refused for c in calls),
            "questions": len(rows), "flips": flips}


def token_stats(rows, budget: int) -> dict:
    calls = [c for _q, cs in rows for c in cs]
    medians = [statistics.median(c.tokens for c in cs) for _q, cs in rows]
    lookup = [m for (q, _cs), m in zip(rows, medians) if q.intent == DOCUMENT_LOOKUP]
    total = sum(c.tokens for c in calls)
    worst = max(c.tokens for c in calls)
    median_all = statistics.median(medians)
    median_lookup = statistics.median(lookup) if lookup else None
    return {
        "total": total, "mean": total / len(calls), "worst_call": worst,
        "median_all": median_all, "per_day_all": per_day(budget, median_all),
        "median_lookup": median_lookup,
        "per_day_lookup": per_day(budget, median_lookup) if median_lookup is not None else None,
        "per_day_worst": per_day(budget, worst),
        "classifier": [c.classifier_tokens for c in calls],
        "generator": [c.generator_tokens for c in calls if c.generator_tokens is not None],
    }


def describe(exc: BaseException) -> str:
    cause = f" (cause {exc.__cause__!r})" if exc.__cause__ is not None else ""
    return f"{type(exc).__name__}: {exc}{cause}".replace("\n", " ")[:500]


async def run_questions(questions, repeats: int, ask, emit=lambda line: None):
    """Asks each question `repeats` times via `ask(question) -> Call`. A failing call stops the run;
    returns (rows, stop_reason or None)."""
    rows = []
    for q in questions:
        calls = []
        rows.append((q, calls))
        for _ in range(repeats):
            try:
                call = await ask(q)
            except Exception as exc:  # provider, embedder, anything: stop, keep the partial report
                return [(rq, cs) for rq, cs in rows if cs], f"{describe(exc)} while asking {q.text!r}"
            calls.append(call)
            emit(f"  call: classifier {call.classifier_tokens} + generator "
                 f"{call.generator_tokens if call.generator_tokens is not None else '(none)'} tokens"
                 f"{' [refused for the context budget]' if call.refused else ''} | {q.text}")
    return rows, None


def report(rows, stop, repeats: int, budget: int, show_answers: bool, emit) -> None:
    if not rows:
        emit("\nno call completed; nothing to report.")
    else:
        emit("\n== routing, per question (correct/repeats; wrong routings listed)")
        for q, cs in rows:
            ok = sum(c.routed == want(q) for c in cs)
            wrong = sorted({str(c.routed) for c in cs if c.routed != want(q)})
            emit(f"{ok}/{len(cs)}{' FLIP' if len({c.routed for c in cs}) > 1 else ''}  {q.text}  [want {want(q)}]"
                 + (f"  got {', '.join(wrong)}" if wrong else "")
                 + (f"  ({sum(c.refused for c in cs)} refused for the context budget)" if any(c.refused for c in cs) else ""))
        r = routing_stats(rows)
        emit(f"routing accuracy: {r['correct']}/{r['calls']} = {r['correct'] / r['calls']:.1%} "
             "(intent, and crop_slug for a lookup; a refused repeat counts by how it was routed)")
        emit(f"flip rate: {r['flips']}/{r['questions']} questions = {r['flips'] / r['questions']:.1%} "
             f"(a question flips if its {repeats} routings are not all the same)")
        emit(f"refused for the context budget: {r['refused']} repeats (not counted as misroutes)")

        t = token_stats(rows, budget)
        emit("\n== tokens (provider-reported input + output)")
        emit(f"classifier per call: {summarise(t['classifier'])}")
        emit(f"generator per call:  {summarise(t['generator'])}")
        emit("per question (classifier + generator, over its repeats):")
        for q, cs in rows:
            emit(f"  {summarise([c.tokens for c in cs])}  {q.text}")
        emit(f"total over {r['calls']} questions asked: {t['total']} tokens; mean per question asked: {t['mean']:.0f}")
        emit(f"median of per-question medians, all questions: {t['median_all']:g} -> "
             f"{fmt_per_day(t['per_day_all'])} questions per member per day at tokens_budget_daily={budget}")
        if t["median_lookup"] is not None:
            emit(f"median, lookup questions only: {t['median_lookup']:g} -> "
                 f"{fmt_per_day(t['per_day_lookup'])} questions per member per day  <- USE THIS FOR THE BUDGET DECISION")
        emit("(declined questions cost one cheap classifier call, so the all-questions figure is optimistic "
             "for a member asking real growing questions.)")
        emit(f"worst single question asked: {t['worst_call']} tokens -> {fmt_per_day(t['per_day_worst'])} per day")
        emit("Decide the budget default from these numbers; do not tune it blind.")

        emit("\n== answers")
        for q, cs in rows:
            if q.text == CUTTINGS_QUESTION or show_answers:
                emit(f"\nQ: {q.text}")
                if q.text == CUTTINGS_QUESTION:
                    emit("(check: the answer must say first and plainly that the corpus has no cuttings source)")
                for n, c in enumerate(cs, 1):
                    emit(f"--- repeat {n}\n{c.answer}")
    if stop:
        emit(f"\nSTOPPED EARLY: {stop}\n"
             "(If this is a usage or spend limit, retrying will not clear it; a 401 means a bad key.)")


# --- wiring ---

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-host", required=True, help="database host to run against (no default, on purpose)")
    parser.add_argument("--db-name", required=True, help="database name to run against (no default, on purpose)")
    parser.add_argument("--repeats", type=int, default=3, help="times each question is asked (default 3)")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help="tokens_budget_daily to divide by the median (default 20000)")
    parser.add_argument("--show-answers", action="store_true", help="print every answer, not only the cuttings one")
    parser.add_argument("--out", metavar="PATH", help="also write the report here (refuses to overwrite)")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.out and os.path.exists(args.out):
        parser.error(f"--out {args.out!r} already exists; it is never overwritten")
    return args


def missing_keys() -> list[str]:
    return [name for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not os.environ.get(name)]


class Emitter:
    """stdout always; the --out file is opened lazily (after every refusal check) and never overwrites."""

    def __init__(self, path: str | None):
        self.path, self.sink = path, None

    def open(self) -> None:
        if self.path:
            try:
                self.sink = open(self.path, "x")
            except OSError as exc:
                sys.exit(f"cannot write --out {self.path!r}: {exc}")

    def __call__(self, line: str) -> None:
        print(line)
        if self.sink:
            self.sink.write(line + "\n")
            self.sink.flush()

    def close(self) -> None:
        if self.sink:
            self.sink.close()


async def run(args, emit) -> int:
    os.environ["DB_HOST"], os.environ["DB_NAME"] = args.db_host, args.db_name
    if not os.environ.get("DB_PASSWORD"):
        sys.exit("DB_PASSWORD is not set; refusing to run.")
    if os.environ.get("EMBEDDER", "openai") != "openai":
        sys.exit("EMBEDDER must be openai: fake vectors carry no meaning, so retrieval would be arbitrary.")
    # imported only now: app.db.db reads DB_HOST/DB_NAME at import time.
    from sqlalchemy import text

    import app.chat.dispatch as dispatch
    from app.chat.embeddings import get_embedder
    from app.chat.llm import get_chat_llm
    from app.crud.retrieval import TopicBudgetExceeded
    from app.db.db import async_session, engine

    engine.sync_engine.echo = False  # the SQL log would bury the report
    try:
        llm, embedder = get_chat_llm(), get_embedder()
        async with async_session() as db:
            count = (await db.execute(text("SELECT count(*) FROM items WHERE published"))).scalar_one()
    except Exception as exc:
        sys.exit(f"cannot start: {describe(exc)}")
    if not count:
        sys.exit(f"{args.db_name} on {args.db_host} has no published documents; seed and reindex it first.")
    emit_open = getattr(emit, "open", None)
    if emit_open:
        emit_open()
    emit(f"target: db {args.db_name} on {args.db_host} ({count} published documents)")
    emit(f"repeats: {args.repeats}; embedder: {embedder.model}; "
         "estimate: each question costs a few thousand tokens (see README; unmeasured until this run)")

    seen = {}  # filled by the wrappers for the call in flight
    real_classify = dispatch.classify

    def recording_classify(question, classifier):
        seen["classification"] = result = real_classify(question, classifier)
        return result

    class RecordingGenerator:
        def __init__(self, inner):
            self.inner = inner

        def generate(self, system, user):
            seen["generator"] = result = self.inner.generate(system, user)
            return result

    async def no_usage(db, member_id, tokens):  # nothing is recorded against a member
        return None

    dispatch.classify = recording_classify
    dispatch.record_usage = no_usage
    llm.generator = RecordingGenerator(llm.generator)

    async def ask(q) -> Call:
        seen.clear()
        refused, answer = False, ""
        try:
            async with async_session() as db:
                answer = (await dispatch.answer(db, 0, q.text, llm, embedder)).answer
        except TopicBudgetExceeded as exc:
            refused, answer = True, f"[refused] {exc}"
        c, g = seen["classification"], seen.get("generator")
        routed = (c.intent, c.crop_slug if c.intent == DOCUMENT_LOOKUP else None)
        return Call(routed, refused, c.tokens, g.tokens if g else None, answer)

    rows, stop = await run_questions(QUESTIONS, args.repeats, ask, emit)
    report(rows, stop, args.repeats, args.budget, args.show_answers, emit)
    return 1 if stop else 0


def main(argv=None) -> int:
    args = parse_args(argv)
    absent = missing_keys()
    if absent:
        sys.exit(f"{' and '.join(absent)} not set; this script calls the real models and refuses to run without them.")
    emit = Emitter(args.out)
    try:
        return asyncio.run(run(args, emit))
    finally:
        emit.close()


if __name__ == "__main__":
    sys.exit(main())
