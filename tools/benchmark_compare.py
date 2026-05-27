#!/usr/bin/env python3
"""파란 보조선 vs 재판독 방식 비교: 정확도 / 시간 / 토큰 / 비용"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocr.claude_parser import (
    parse_duty_image_with_claude,
    send_claude_request,
    build_ssl_context,
    DEFAULT_ANTHROPIC_MODEL,
)

SAMPLE  = ROOT / "samples" / "주희_duty_june.jpeg"
ANSWER  = ROOT / "samples" / "Correction_result.xlsx"

# claude-sonnet-4 pricing (per 1M tokens, 2025)
PRICE_INPUT  = 3.0   # $3 / 1M input tokens
PRICE_OUTPUT = 15.0  # $15 / 1M output tokens

NAME_ALIASES = {"장혜진": "장해진", "유정수": "유정숙", "양임향": "양일향"}
SHIFT_NORM   = {"d":"D","e":"E","n":"N","s":"S","y":"Y","D":"D","E":"E","N":"N",
                "S":"S","Y":"Y","off":"OFF","OFF":"OFF","of":"OFF"}


def load_answer() -> dict[str, list[str | None]]:
    wb = openpyxl.load_workbook(ANSWER)
    ws = wb.active
    out: dict[str, list[str | None]] = {}
    for row in ws.iter_rows(values_only=True):
        if row[0] is None:
            continue
        name = str(row[1]).strip()
        shifts = [SHIFT_NORM.get(str(v).strip(), str(v).strip().upper()) if v else None for v in row[2:32]]
        out[name] = shifts
    return out


def score(result: dict, answer: dict[str, list[str | None]]) -> tuple[int, int]:
    raw = {r["name"]: r["shifts"] for r in result["rows"]}
    rows: dict[str, list] = dict(raw)
    for ans_name, ocr_name in NAME_ALIASES.items():
        if ocr_name in raw:
            rows[ans_name] = raw[ocr_name]
    correct = total = 0
    for name, expected in answer.items():
        for exp, pred in zip(expected, rows.get(name, [None] * 30)):
            if exp is None:
                continue
            total += 1
            if SHIFT_NORM.get(pred, pred) == exp:
                correct += 1
    return correct, total


def run_variant(label: str, image_bytes: bytes, answer: dict, **kwargs) -> dict:
    t0 = time.perf_counter()
    result = parse_duty_image_with_claude(image_bytes, SAMPLE.name, **kwargs)
    elapsed = (time.perf_counter() - t0) * 1000

    correct, total = score(result, answer)
    acc = correct / total * 100 if total else 0

    tmpl = result.get("template", {})
    calls     = 1 + len(tmpl.get("refinedRows", []))
    in_tok    = tmpl.get("inputTokens", 0)
    out_tok   = tmpl.get("outputTokens", 0)
    cost_usd  = (in_tok * PRICE_INPUT + out_tok * PRICE_OUTPUT) / 1_000_000

    return {
        "label":   label,
        "acc":     acc,
        "correct": correct,
        "total":   total,
        "ms":      elapsed,
        "calls":   calls,
        "in_tok":  in_tok,
        "out_tok": out_tok,
        "cost":    cost_usd,
    }


def fmt_row(r: dict) -> str:
    return (
        f"  {r['label']:<32} | {r['acc']:>6.1f}% ({r['correct']}/{r['total']})"
        f" | {r['ms']:>7.0f}ms | {r['calls']} call(s)"
        f" | in={r['in_tok']:>5} out={r['out_tok']:>4} tok"
        f" | ${r['cost']:.4f}"
    )


def main() -> None:
    image_bytes = SAMPLE.read_bytes()
    answer = load_answer()

    variants = [
        ("베이스라인 (보조선 없음)",
         dict(use_row_guides=False)),
        ("보조선 2400px (현재 기본값)",
         dict(use_row_guides=True, guide_image_width=2400)),
        ("보조선 1600px",
         dict(use_row_guides=True, guide_image_width=1600)),
        ("보조선 1200px",
         dict(use_row_guides=True, guide_image_width=1200)),
    ]

    results = []
    for label, kwargs in variants:
        print(f"  [{len(results)+1}/{len(variants)}] {label} ...")
        r = run_variant(label, image_bytes, answer, **kwargs)
        results.append(r)
        print(f"    → {r['acc']:.1f}%  {r['ms']:.0f}ms")

    print()
    print("─" * 100)
    print(f"  {'방식':<32} | {'정확도':>14} | {'시간':>9} | {'호출':>8} | {'토큰':>20} | {'비용'}")
    print("─" * 100)
    for r in results:
        print(fmt_row(r))
    print("─" * 100)

    out = ROOT / "scratch" / "ocr-debug" / "benchmark_compare.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {out.name}")


if __name__ == "__main__":
    main()
