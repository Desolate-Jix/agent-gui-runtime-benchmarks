"""只用训练结果离线冻结类别组合；不联网，不读取保留集结果。"""
from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import sys
import unittest


CHOICES = {"condition_met", "condition_not_met", "evidence_insufficient"}
SELECTION_RULE = [
    "per-category exact accuracy descending",
    "false accepts ascending",
    "false rejects ascending",
    "median latency ascending",
    "input tokens ascending",
]


def score(rows):
    returned = [r for r in rows if r.get("status") == "completed" and r.get("choice") in CHOICES]
    correct = sum(r["choice"] == r["expected"] for r in returned)
    latencies = [r["http_elapsed_ms"] for r in rows
                 if type(r.get("http_elapsed_ms")) in (int, float)
                 and math.isfinite(r["http_elapsed_ms"]) and r["http_elapsed_ms"] >= 0]
    known_tokens, unknown = 0, 0
    for row in rows:
        usage = row.get("usage")
        if usage is None:
            unknown += 1
        elif type(usage) is not dict or type(usage.get("input_tokens")) is not int or usage["input_tokens"] < 0:
            raise ValueError("selection_usage_invalid")
        else:
            known_tokens += usage["input_tokens"]
    return {"cases": len(rows), "returned": len(returned), "exact_correct": correct,
            "accuracy": correct / len(rows) if rows else None,
            "false_accepts": sum(r["choice"] == "condition_met" and r["expected"] != "condition_met" for r in returned),
            "false_rejects": sum(r["expected"] == "condition_met" and r["choice"] != "condition_met" for r in returned),
            "errors": len(rows) - len(returned), "timeouts": sum(r.get("error") == "timeout" for r in rows),
            "status_counts": dict(sorted(__import__("collections").Counter(r["status"] for r in rows).items())),
            "p50_http_ms": statistics.median(latencies) if latencies else None, "http_latency_n": len(latencies),
            "input_tokens": known_tokens, "unknown_usage_requests": unknown,
            "ranking_input_tokens": None if unknown else known_tokens}


def rank(value):
    return (-Fraction(value["exact_correct"], value["cases"]), value["false_accepts"], value["false_rejects"],
            math.inf if value["p50_http_ms"] is None else value["p50_http_ms"],
            math.inf if value["ranking_input_tokens"] is None else value["ranking_input_tokens"])


def select_training(suite, variants, plan, run, rows, hashes):
    if (plan.get("schema") != "decision_prompt_comparison.v1" or plan.get("selection") != SELECTION_RULE
            or plan.get("training", {}).get("split") != "train"):
        raise ValueError("selection_plan_invalid")
    for key in ("suite_sha256", "variants_sha256"):
        if plan.get(key) != hashes.get(key):
            raise ValueError("selection_frozen_hash_conflict")
    config = run.get("config", {})
    repeats = plan["training"].get("repeats")
    if (run.get("schema") != "decision_benchmark_run.v1" or config.get("split") != "train"
            or type(repeats) is not int or repeats < 1 or config.get("repeats") != repeats
            or config.get("seed") != plan.get("seed")
            or config.get("manifest_sha256") != hashes["suite_sha256"]
            or config.get("variants_sha256") != hashes["variants_sha256"]):
        raise ValueError("selection_train_run_conflict")
    if (suite.get("schema") != "decision_benchmark.v1" or type(variants) is not dict
            or variants.get("baseline") != {} or any(type(v) is not dict for v in variants.values())):
        raise ValueError("selection_inputs_invalid")
    cases = {c["id"]: c for c in suite["cases"] if c["split"] == "train"}
    if not cases or len(cases) != sum(c["split"] == "train" for c in suite["cases"]):
        raise ValueError("selection_train_cases_invalid")
    categories = sorted({c["category"] for c in cases.values()})
    if any(set(v) - set(categories) or any(type(t) is not str for t in v.values()) for v in variants.values()):
        raise ValueError("selection_variants_invalid")
    grid = {(case, variant, repeat) for case in cases for variant in variants for repeat in range(repeats)}
    if (len(grid) != plan["training"].get("requests") or type(rows) is not list
            or len(rows) != len(grid) or len(run.get("tasks", [])) != len(grid)):
        raise ValueError("selection_training_incomplete")
    tasks, request_ids = {}, set()
    for task in run["tasks"]:
        key = (task["case"], task["variant"], task["repeat"])
        if (key not in grid or key in tasks or not task.get("request_id")
                or task["request_id"] in request_ids or type(task.get("request_sha256")) is not str
                or len(task["request_sha256"]) != 64):
            raise ValueError("selection_task_binding_invalid")
        tasks[key] = task
        request_ids.add(task["request_id"])
    seen = set()
    for row in rows:
        key = (row["case"], row["variant"], row["repeat"])
        if key not in tasks or key in seen:
            raise ValueError("selection_train_row_binding_invalid")
        seen.add(key)
        task, case = tasks[key], cases[row["case"]]
        if (any(row.get(k) != task[k] for k in ("request_id", "request_sha256"))
                or any(row.get(k) != case[k] for k in ("expected", "category", "group_id"))):
            raise ValueError("selection_train_row_binding_invalid")
        if (row.get("status") not in {"completed", "error", "unknown", "refusal", "budget_exceeded", "timeout"}
                or row["status"] == "completed" and row.get("choice") not in CHOICES):
            raise ValueError("selection_training_unfinished_or_invalid")
    selected, details, mix = {}, {}, {}
    for category in categories:
        scores = {name: score([r for r in rows if r["category"] == category and r["variant"] == name])
                  for name in sorted(variants)}
        same = [name for name in variants if name != "baseline" and (category not in variants[name]
                or all(tasks[(case, name, repeat)]["request_sha256"] == tasks[(case, "baseline", repeat)]["request_sha256"]
                       for case in cases if cases[case]["category"] == category for repeat in range(repeats)))]
        eligible = [name for name in variants if name not in same]
        order = sorted(eligible, key=lambda name: (rank(scores[name]), name != "baseline", name))
        winner = order[0]
        selected[category] = winner
        if winner != "baseline":
            mix[category] = variants[winner][category]
        details[category] = {"selected_variant": winner, "scores": scores, "eligible_ranking": order,
                             "same_request_as_baseline": sorted(same),
                             "tied_best_variants": sorted(name for name in eligible if rank(scores[name]) == rank(scores[winner]))}
    return {"schema": "decision_training_selection.v1", "stage": "train_frozen_pending_validation",
            "selection_split": "train", "inputs": dict(hashes), "selection_rule": list(SELECTION_RULE),
            "metric_definitions": {
                "accuracy": "Exact three-way matches / every frozen train row, including errors/timeouts/refusals/unknowns.",
                "false_accepts": "Returned condition_met when expected is not condition_met.",
                "false_rejects": "Returned nonpass on expected condition_met; errors are separate and remain in accuracy denominator, matching runner summarize().",
                "p50_http_ms": "Median of every available finite HTTP latency, including failed requests; not only correct/returned rows.",
                "input_tokens": "Known input-token sum; any unknown usage ranks as unavailable, never as zero-token savings.",
                "ties": "Baseline wins a complete five-key tie; remaining ties use variant name for determinism. Byte-identical category requests retain baseline."},
            "selected": selected, "categories": details, "has_changes": bool(mix),
            "runner_variants": {"baseline": {}, **({"frozen_mix": mix} if mix else {})},
            "validation_results_read": False, "test_results_read": False,
            "validation_rule": plan.get("validation"), "heldout_rule": plan.get("heldout"),
            "limitations": "Training-only selection; small training gains are not general accuracy evidence. Validation may reject changes; test requires a later final freeze."}


def _self_fixture():
    cases = [
        {"id": "f-pos", "split": "train", "category": "input_field", "group_id": "field", "expected": "condition_met"},
        {"id": "f-neg", "split": "train", "category": "input_field", "group_id": "field", "expected": "condition_not_met"},
        {"id": "g-pos", "split": "train", "category": "graphic", "group_id": "graphic", "expected": "condition_met"},
        {"id": "g-neg", "split": "train", "category": "graphic", "group_id": "graphic", "expected": "condition_not_met"},
        {"id": "held-out", "split": "test", "category": "graphic", "group_id": "held", "expected": "evidence_insufficient"},
    ]
    suite = {"schema": "decision_benchmark.v1", "suite_id": "synthetic", "cases": cases}
    variants = {"baseline": {}, "short": {"input_field": "short field"},
                "region_first": {"input_field": "region field", "graphic": "region graphic"}}
    hashes = {"suite_sha256": "a" * 64, "variants_sha256": "b" * 64,
              "train_sha256": "c" * 64, "run_sha256": "d" * 64, "comparison_plan_sha256": "e" * 64}
    plan = {"schema": "decision_prompt_comparison.v1", **{k: hashes[k] for k in ("suite_sha256", "variants_sha256")},
            "seed": 11, "training": {"split": "train", "repeats": 1, "requests": 12},
            "selection": list(SELECTION_RULE)}
    config = {"split": "train", "repeats": 1, "seed": 11,
              "manifest_sha256": hashes["suite_sha256"], "variants_sha256": hashes["variants_sha256"]}
    tasks, rows = [], []
    for case in cases[:4]:
        for variant in variants:
            task = {"case": case["id"], "variant": variant, "repeat": 0,
                    "request_id": "synthetic-" + case["id"] + "-" + variant,
                    "request_sha256": sha256((case["id"] + (variant if case["category"] in variants[variant] else "baseline")).encode("ascii")).hexdigest()}
            tasks.append(task)
            answer = case["expected"]
            if case["id"] == "f-pos" and variant != "short":
                answer = "condition_not_met"
            if case["id"] == "g-pos" and variant != "region_first":
                answer = "condition_not_met"
            rows.append({**task, **{k: case[k] for k in ("group_id", "category", "expected")},
                         "status": "completed", "choice": answer, "http_elapsed_ms": 100.0,
                         "usage": {"input_tokens": 10, "output_tokens": 0}})
    run = {"schema": "decision_benchmark_run.v1", "run_id": "0" * 32, "config": config, "tasks": tasks}
    return suite, variants, plan, run, rows, hashes


class SelectionSelfTest(unittest.TestCase):
    def test_errors_and_timeouts_stay_in_exact_accuracy_denominator(self):
        rows = [
            {"status": "completed", "choice": "condition_met", "expected": "condition_met", "http_elapsed_ms": 10, "usage": {"input_tokens": 5}},
            {"status": "unknown", "choice": None, "expected": "condition_met", "error": "timeout", "http_elapsed_ms": 80, "usage": None},
            {"status": "error", "choice": None, "expected": "condition_not_met", "error": "invalid_reply", "http_elapsed_ms": 20, "usage": {"input_tokens": 7}},
        ]
        actual = score(rows)
        self.assertEqual((actual["cases"], actual["exact_correct"], actual["errors"]), (3, 1, 2))
        self.assertEqual(actual["accuracy"], 1 / 3)
        self.assertEqual(actual["p50_http_ms"], 20)
        self.assertEqual(actual["input_tokens"], 12)
        self.assertEqual(actual["unknown_usage_requests"], 1)
        self.assertIsNone(actual["ranking_input_tokens"])

    def test_three_way_truth_and_false_accept_reject_counts(self):
        rows = [{"status": "completed", "choice": choice, "expected": expected,
                 "http_elapsed_ms": 10, "usage": {"input_tokens": 1}}
                for choice, expected in [("condition_met", "evidence_insufficient"),
                                         ("evidence_insufficient", "condition_met"),
                                         ("condition_not_met", "evidence_insufficient")]]
        actual = score(rows)
        self.assertEqual((actual["exact_correct"], actual["false_accepts"], actual["false_rejects"]), (0, 1, 1))

    def test_all_five_selection_keys_have_declared_priority(self):
        base = {"cases": 10, "exact_correct": 5, "false_accepts": 3, "false_rejects": 3,
                "p50_http_ms": 100, "ranking_input_tokens": 100}
        better = [
            {"exact_correct": 6, "false_accepts": 9, "false_rejects": 9, "p50_http_ms": 900, "ranking_input_tokens": 900},
            {"false_accepts": 2, "false_rejects": 9, "p50_http_ms": 900, "ranking_input_tokens": 900},
            {"false_rejects": 2, "p50_http_ms": 900, "ranking_input_tokens": 900},
            {"p50_http_ms": 99, "ranking_input_tokens": 900},
            {"ranking_input_tokens": 99},
        ]
        for change in better:
            with self.subTest(change=change):
                self.assertLess(rank({**base, **change}), rank(base))
        self.assertGreater(rank({**base, "ranking_input_tokens": None}), rank(base))

    def test_training_categories_form_one_mix_and_keep_baseline_control(self):
        actual = select_training(*_self_fixture())
        self.assertEqual(actual.get("selected"), {"graphic": "region_first", "input_field": "short"})
        self.assertEqual(actual.get("runner_variants"), {"baseline": {}, "frozen_mix": {"graphic": "region graphic", "input_field": "short field"}})
        self.assertEqual(actual.get("inputs"), _self_fixture()[-1])

    def test_exact_ties_and_identical_category_prompts_retain_baseline(self):
        fixture = list(_self_fixture())
        for row in fixture[4]:
            row["choice"] = row["expected"]
        actual = select_training(*fixture)
        self.assertEqual(actual.get("selected"), {"graphic": "baseline", "input_field": "baseline"})
        self.assertEqual(actual.get("runner_variants"), {"baseline": {}})

    def test_unfinished_missing_duplicate_or_heldout_rows_cannot_freeze(self):
        for kind in ("prepared", "missing", "duplicate", "heldout"):
            f = list(_self_fixture())
            if kind == "prepared":
                f[4][0]["status"] = "prepared"
            elif kind == "missing":
                f[4].pop()
            elif kind == "duplicate":
                f[4][-1] = deepcopy(f[4][0])
            else:
                f[4][0]["case"] = "held-out"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                select_training(*f)

    def test_truth_request_and_frozen_hash_mismatches_cannot_freeze(self):
        for kind in ("expected", "category", "request_sha256", "run_hash", "plan_hash"):
            f = list(_self_fixture())
            if kind == "run_hash":
                f[3]["config"]["manifest_sha256"] = "0" * 64
            elif kind == "plan_hash":
                f[2]["variants_sha256"] = "0" * 64
            else:
                f[4][0][kind] = "wrong"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                select_training(*f)

    def test_validation_run_or_changed_selection_rule_is_rejected(self):
        for kind in ("split", "rule"):
            f = list(_self_fixture())
            if kind == "split":
                f[3]["config"]["split"] = "validation"
            else:
                f[2]["selection"] = list(reversed(SELECTION_RULE))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                select_training(*f)


def _read_snapshot(path):
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), sha256(raw).hexdigest()


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--train-run", type=Path, default=root / "train-run-01")
    parser.add_argument("--suite", type=Path, default=root / "suite-v2/suite.json")
    parser.add_argument("--variants", type=Path, default=root / "variants-v1.json")
    parser.add_argument("--comparison-plan", type=Path, default=root / "comparison-plan.json")
    parser.add_argument("--output", type=Path, default=root / "frozen-selection.json")
    parser.add_argument("--print-variants", type=Path)
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SelectionSelfTest))
        return 0 if result.wasSuccessful() else 1
    if args.print_variants:
        frozen, _ = _read_snapshot(args.print_variants)
        if frozen.get("schema") != "decision_training_selection.v1" or frozen.get("selection_split") != "train":
            raise ValueError("selection_frozen_artifact_invalid")
        print(json.dumps(frozen["runner_variants"], ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    paths = {"suite_sha256": args.suite, "variants_sha256": args.variants,
             "comparison_plan_sha256": args.comparison_plan, "run_sha256": args.train_run / "run.json",
             "train_sha256": args.train_run / "results.json"}
    snapshots = {key: _read_snapshot(path) for key, path in paths.items()}
    value = select_training(*(snapshots[key][0] for key in
        ("suite_sha256", "variants_sha256", "comparison_plan_sha256", "run_sha256", "train_sha256")),
        {key: pair[1] for key, pair in snapshots.items()})
    if any(sha256(path.read_bytes()).hexdigest() != snapshots[key][1] for key, path in paths.items()):
        raise ValueError("selection_inputs_changed_during_read")
    value["script_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    value["source_paths"] = {key: str(path.resolve()) for key, path in paths.items()}
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    print(json.dumps({"output": str(args.output.resolve()), "sha256": sha256(payload.encode("utf-8")).hexdigest(),
                      "selected": value["selected"], "has_changes": value["has_changes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("selection_failed: " + str(error), file=sys.stderr)
        raise SystemExit(2) from None
