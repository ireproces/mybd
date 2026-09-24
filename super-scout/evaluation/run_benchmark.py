"""Benchmark di Super-Scout: efficacia, coerenza, efficienza.

Esegue ogni formulazione di ogni caso di `benchmark.json` attraverso lo stesso
oggetto `ScoutAssistant` che serve l'API (niente HTTP: il benchmark gira dove
girano il modello e il database), calcola la verita' con il Cypher scritto a
mano e confronta. Le metriche sono quelle dichiarate nel piano di valutazione
del progetto, misurate su scala reale:

- esattezza dei risultati: uguaglianza dell'insieme dei nomi restituiti con la
  verita' (e dei valori, quando la colonna di ordinamento e' confrontabile);
  precision e recall sui nomi;
- correttezza sintattica: Cypher valido al primo tentativo, tentativi usati,
  errori Neo4j e interventi dei guardrail, per classe;
- copertura dei concetti: i costrutti che il Cypher deve contenere;
- risposte sbagliate in silenzio: righe restituite ma diverse dalla verita' -
  la metrica della tesi del progetto;
- coerenza: stesso insieme tra le formulazioni di un caso e tra esecuzioni;
- rifiuti corretti sulle metriche assenti e sulle scritture;
- efficienza per fase (mediana e p90 dei `timings`).

Uso, dal container dell'API (o con PYTHONPATH sulla radice del progetto):
    python evaluation/run_benchmark.py --runs 1 --tag baseline
    GUARDRAILS=off python evaluation/run_benchmark.py --tag no-guardrails
    OPENAI_MODEL=gpt-4o python evaluation/run_benchmark.py --tag gpt-4o
Risultati in evaluation/results/<tag>.json (grezzi) e <tag>.md (tabelle).
"""

import argparse
import json
import logging
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)  # avvisi di deprecazione del driver

import openai  # noqa: E402

from app.config import settings  # noqa: E402
from app.graph import GraphStore  # noqa: E402
from app.rag import ScoutAssistant, _ordering_column  # noqa: E402


def _names(rows: list[dict[str, Any]], truth_names: set[str]) -> tuple[str | None, list[str]]:
    """La colonna delle righe che contiene i nomi della verita', e i suoi valori."""
    best, best_hits = None, 0
    for key in (rows[0] if rows else {}):
        values = [str(r.get(key)) for r in rows if isinstance(r.get(key), str)]
        hits = len(set(values) & truth_names)
        if hits > best_hits:
            best, best_hits = key, hits
    if best is None:
        return None, []
    return best, [str(r.get(best)) for r in rows]


def _close(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.011
    except (TypeError, ValueError):
        return str(a) == str(b)


def evaluate(case: dict, answer: dict, truth_rows: list[dict] | None) -> dict[str, Any]:
    rows = answer.get("rows") or []
    trace = answer.get("trace") or []
    kinds = [k for step in trace for k in step.get("kinds", [])]
    result: dict[str, Any] = {
        "mode": answer.get("mode"),
        "rows": len(rows),
        "attempts": (answer.get("timings") or {}).get("attempts") or (len(trace) + 1 if answer.get("mode") == "failed" else None),
        "first_attempt_ok": not trace,
        "neo4j_errors": sum(1 for k in kinds if k == "Neo4jError"),
        "guard_kinds": [k for k in kinds if k != "Neo4jError"],
        "timings": answer.get("timings") or {},
        "cypher": answer.get("cypher"),
    }
    cypher = answer.get("cypher") or ""
    concepts = case.get("concepts") or []
    result["concept_coverage"] = (
        sum(1 for c in concepts if re.search(c, cypher, re.IGNORECASE)) / len(concepts) if concepts and cypher else (1.0 if not concepts else 0.0)
    )
    expected_mode = case.get("expected_mode")
    if expected_mode:
        allowed = expected_mode if isinstance(expected_mode, list) else [expected_mode]
        result["correct"] = answer.get("mode") in allowed
        result["kind"] = "refusal"
        return result
    if truth_rows is None:
        result["correct"] = answer.get("mode") == "graph_rag" and len(rows) >= case.get("min_rows", 1)
        result["kind"] = "presence"
        return result
    result["kind"] = "set"
    truth_names = {str(r["name"]) for r in truth_rows}
    truth_values = {str(r["name"]): r.get("value") for r in truth_rows}
    key, names = _names(rows, truth_names)
    got = set(names)
    tp = len(got & truth_names)
    result["precision"] = tp / len(got) if got else 0.0
    result["recall"] = tp / len(truth_names) if truth_names else 1.0
    result["names_exact"] = got == truth_names
    value_key = _ordering_column(cypher, rows) if rows else None
    if result["names_exact"] and value_key and key and any(v is not None for v in truth_values.values()):
        pairs = [(r.get(value_key), truth_values.get(str(r.get(key)))) for r in rows]
        result["values_exact"] = all(_close(a, b) for a, b in pairs if b is not None)
    else:
        result["values_exact"] = None
    result["correct"] = result["names_exact"]
    result["silent_wrong"] = answer.get("mode") == "graph_rag" and len(rows) > 0 and not result["names_exact"]
    result["empty_wrong"] = answer.get("mode") == "graph_rag" and len(rows) == 0 and bool(truth_names)
    result["failed"] = answer.get("mode") == "failed"
    result["names"] = sorted(got)
    return result


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def summarize(records: list[dict], cases: list[dict]) -> str:
    sets = [r for r in records if r["kind"] == "set"]
    refusals = [r for r in records if r["kind"] == "refusal"]
    presence = [r for r in records if r["kind"] == "presence"]
    answered = [r for r in records if r["kind"] != "refusal"]
    lines = []
    lines.append(f"Domande eseguite: {len(records)} ({len({(r['case'], r['form']) for r in records})} formulazioni distinte, "
                 f"{len(cases)} casi, {len({r['run'] for r in records})} esecuzioni)\n")
    lines.append("## Efficacia\n")
    lines.append("| Metrica | Valore |\n|---|---:|")
    if sets:
        lines.append(f"| Insieme dei nomi esatto | {sum(r['correct'] for r in sets)}/{len(sets)} ({100*sum(r['correct'] for r in sets)/len(sets):.0f}%) |")
        with_values = [r for r in sets if r.get("values_exact") is not None]
        if with_values:
            lines.append(f"| Nomi e valori esatti (dove confrontabili) | {sum(r['values_exact'] for r in with_values)}/{len(with_values)} |")
        lines.append(f"| Precision media sui nomi | {100*statistics.mean(r['precision'] for r in sets):.1f}% |")
        lines.append(f"| Recall media sui nomi | {100*statistics.mean(r['recall'] for r in sets):.1f}% |")
        lines.append(f"| **Risposte sbagliate in silenzio** (righe restituite, insieme diverso) | {sum(r['silent_wrong'] for r in sets)}/{len(sets)} |")
        lines.append(f"| Risposte vuote a torto | {sum(r['empty_wrong'] for r in sets)}/{len(sets)} |")
        lines.append(f"| Traduzioni fallite (dopo le riparazioni) | {sum(r['failed'] for r in sets)}/{len(sets)} |")
    if presence:
        lines.append(f"| Cammini nel grafo trovati | {sum(r['correct'] for r in presence)}/{len(presence)} |")
    if refusals:
        lines.append(f"| Rifiuti corretti (metriche assenti, scritture) | {sum(r['correct'] for r in refusals)}/{len(refusals)} |")
    lines.append(f"| Copertura dei concetti nel Cypher (media) | {100*statistics.mean(r['concept_coverage'] for r in answered):.1f}% |")
    lines.append("")
    lines.append("## Correttezza sintattica e riparazioni\n")
    attempts = [r["attempts"] for r in answered if r["attempts"]] or [0]
    lines.append("| Metrica | Valore |\n|---|---:|")
    lines.append(f"| Cypher accettato al primo tentativo | {sum(r['first_attempt_ok'] for r in answered)}/{len(answered)} ({100*sum(r['first_attempt_ok'] for r in answered)/len(answered):.0f}%) |")
    lines.append(f"| Tentativi medi | {statistics.mean(attempts):.2f} |")
    lines.append(f"| Distribuzione tentativi | {dict(sorted(Counter(attempts).items()))} |")
    lines.append(f"| Errori di sintassi Neo4j (totale) | {sum(r['neo4j_errors'] for r in answered)} |")
    guard_counter = Counter(k for r in answered for k in r["guard_kinds"])
    lines.append(f"| Interventi dei guardrail (totale) | {sum(guard_counter.values())} |")
    lines.append("")
    if guard_counter:
        lines.append("Guardrail per classe:\n")
        lines.append("| Guardrail | Interventi |\n|---|---:|")
        for kind, n in guard_counter.most_common():
            lines.append(f"| `{kind}` | {n} |")
        lines.append("")
    # Coerenza tra formulazioni e tra esecuzioni.
    lines.append("## Coerenza\n")
    by_case: dict[str, dict[tuple, list[str]]] = defaultdict(dict)
    for r in sets:
        by_case[r["case"]][(r["form"], r["run"])] = r["names"]
    forms_agree = runs_agree = forms_total = runs_total = 0
    for case_id, outcomes in by_case.items():
        runs = {run for _, run in outcomes}
        forms = {form for form, _ in outcomes}
        for run in runs:
            observed = [tuple(outcomes[(f, run)]) for f in forms if (f, run) in outcomes]
            if len(observed) > 1:
                forms_total += 1
                forms_agree += len(set(observed)) == 1
        for form in forms:
            observed = [tuple(outcomes[(form, run)]) for run in runs if (form, run) in outcomes]
            if len(observed) > 1:
                runs_total += 1
                runs_agree += len(set(observed)) == 1
    lines.append("| Metrica | Valore |\n|---|---:|")
    lines.append(f"| Casi con formulazioni diverse che danno lo stesso insieme | {forms_agree}/{forms_total} |" if forms_total else "| Formulazioni multiple | n/d |")
    lines.append(f"| Formulazioni che danno lo stesso insieme in esecuzioni ripetute | {runs_agree}/{runs_total} |" if runs_total else "| Esecuzioni ripetute | n/d (una sola esecuzione) |")
    lines.append("")
    lines.append("## Efficienza (per fase, ms)\n")
    lines.append("| Fase | Mediana | p90 | Max |\n|---|---:|---:|---:|")
    for field, label in [("generate_ms", "Generazione Cypher (riparazioni comprese)"), ("database_ms", "Esecuzione Neo4j"),
                         ("evidence_ms", "Sotto-grafo di evidenza"), ("explain_ms", "Spiegazione"), ("total_ms", "Totale")]:
        values = [r["timings"][field] for r in answered if r["timings"].get(field) is not None]
        if values:
            lines.append(f"| {label} | {statistics.median(values):,.0f} | {percentile(values, 0.9):,.0f} | {max(values):,.0f} |")
    lines.append("")
    lines.append("## Dettaglio per caso\n")
    lines.append("| Caso | Gruppo | Forme x run | Esatti | Prec. | Rec. | Silenziosi | 1° tentativo | Tent. medi |\n|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for case in cases:
        rs = [r for r in records if r["case"] == case["id"]]
        if not rs:
            continue
        s = [r for r in rs if r["kind"] == "set"]
        prec = f"{100*statistics.mean(r['precision'] for r in s):.0f}%" if s else "—"
        rec = f"{100*statistics.mean(r['recall'] for r in s):.0f}%" if s else "—"
        silent = sum(r.get("silent_wrong", False) for r in s) if s else "—"
        tries = [r["attempts"] for r in rs if r["attempts"]]
        lines.append(f"| `{case['id']}` | {case['group']} | {len(rs)} | {sum(r['correct'] for r in rs)}/{len(rs)} | {prec} | {rec} | {silent} | "
                     f"{sum(r['first_attempt_ok'] for r in rs)}/{len(rs)} | {statistics.mean(tries) if tries else 0:.1f} |")
    wrong = [r for r in sets if not r["correct"]]
    if wrong:
        lines.append("\n## Divergenze\n")
        for r in wrong:
            lines.append(f"- `{r['case']}` forma {r['form'] + 1} run {r['run'] + 1}: mode={r['mode']}, righe={r['rows']}, "
                         f"precision={100*r['precision']:.0f}%, recall={100*r['recall']:.0f}%")
    return "\n".join(lines)


def _ask(assistant: ScoutAssistant, question: str) -> dict:
    """Una domanda, con attesa e nuovo tentativo sui limiti di velocita' del modello.

    Il prompt e' lungo e ogni domanda costa da due a quattro chiamate: 200.000
    token al minuto si esauriscono in poche domande, e un 429 a meta' benchmark
    non deve buttare via un'ora di misure.
    """
    for attempt in range(6):
        try:
            return assistant.answer(question)
        except openai.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"   rate limit del modello: attendo {wait}s", flush=True)
            time.sleep(wait)
    return assistant.answer(question)


def _checkpoint(tag: str, records: list[dict], settings_obj, runs: int) -> None:
    """Salva i risultati grezzi dopo ogni domanda: un'interruzione non perde nulla."""
    out = ROOT / "evaluation" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{tag}.json").write_text(json.dumps({
        "tag": tag, "model": settings_obj.openai_model, "guardrails": settings_obj.guardrails, "runs": runs,
        "partial": True, "records": records,
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=1, help="esecuzioni per formulazione (coerenza tra esecuzioni)")
    parser.add_argument("--tag", default="baseline", help="nome dei file di risultato")
    parser.add_argument("--cases", default="", help="solo questi id, separati da virgola")
    parser.add_argument("--forms", type=int, default=0, help="al massimo N formulazioni per caso (0 = tutte)")
    parser.add_argument("--resume", action="store_true", help="riprende un'esecuzione interrotta con lo stesso tag")
    parser.add_argument("--pause", type=float, default=8.0, help="secondi di pausa tra una domanda e l'altra (limite di token al minuto del modello)")
    args = parser.parse_args()

    benchmark = json.loads((ROOT / "evaluation" / "benchmark.json").read_text(encoding="utf-8"))
    cases = benchmark["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    graph = GraphStore()
    assistant = ScoutAssistant(graph)
    if not assistant.client:
        sys.exit("OPENAI_API_KEY non configurata: il benchmark ha bisogno del modello.")
    truths: dict[str, list[dict] | None] = {c["id"]: (graph.query(c["truth"]) if c.get("truth") else None) for c in cases}

    records: list[dict] = []
    previous = ROOT / "evaluation" / "results" / f"{args.tag}.json"
    if args.resume and previous.exists():
        records = json.loads(previous.read_text(encoding="utf-8"))["records"]
        print(f"riprendo da {len(records)} domande gia' eseguite", flush=True)
    done = {(r["case"], r["form"], r["run"]) for r in records}
    started = time.time()
    for run in range(args.runs):
        for case in cases:
            forms = case["forms"][: args.forms] if args.forms else case["forms"]
            for index, question in enumerate(forms):
                if (case["id"], index, run) in done:
                    continue
                answer = _ask(assistant, question)
                record = evaluate(case, answer, truths[case["id"]])
                record.update({"case": case["id"], "group": case["group"], "form": index, "run": run, "question": question,
                               "answer": (answer.get("answer") or "")[:400]})
                records.append(record)
                _checkpoint(args.tag, records, settings, args.runs)
                time.sleep(args.pause)
                flag = "OK " if record["correct"] else "!! "
                print(f"{flag} run={run + 1} {case['id']:26} f{index + 1} mode={record['mode']:<10} rows={record['rows']:<5} "
                      f"att={record['attempts']} " + (f"P={100*record.get('precision', 0):.0f}% R={100*record.get('recall', 0):.0f}%" if record["kind"] == "set" else ""),
                      flush=True)
    elapsed = time.time() - started
    graph.close()

    out = ROOT / "evaluation" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{args.tag}.json").write_text(json.dumps({
        "tag": args.tag, "model": settings.openai_model, "guardrails": settings.guardrails, "runs": args.runs,
        "partial": False, "elapsed_s": round(elapsed), "records": records,
    }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    report = (f"# Benchmark `{args.tag}`\n\nModello: `{settings.openai_model}`; guardrail: {'attivi' if settings.guardrails else 'DISATTIVATI'}; "
              f"durata: {elapsed/60:.1f} min.\n\n" + summarize(records, cases))
    (out / f"{args.tag}.md").write_text(report, encoding="utf-8")
    print("\n" + report)


if __name__ == "__main__":
    main()
