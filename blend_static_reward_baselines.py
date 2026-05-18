#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List

import numpy as np
import pandas as pd


DEFAULT_V2 = "/home/ubuntu/myren/SF110/artifacts/static_reward_baseline_v2/oof_predictions.csv"
DEFAULT_V3 = "/home/ubuntu/myren/SF110/artifacts/static_reward_baseline_v3/oof_predictions.csv"
DEFAULT_OUTPUT_DIR = "/home/ubuntu/myren/SF110/artifacts/static_reward_blend"


def _spearman(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    true_rank = pd.Series(y_true).rank(method="average").to_numpy()
    pred_rank = pd.Series(y_pred).rank(method="average").to_numpy()
    if np.std(true_rank) == 0.0 or np.std(pred_rank) == 0.0:
        return 0.0
    return float(np.corrcoef(true_rank, pred_rank)[0, 1])


def _pairwise_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    correct = 0
    total = 0
    for i in range(len(y_true)):
        for j in range(i + 1, len(y_true)):
            if y_true[i] == y_true[j]:
                continue
            total += 1
            true_sign = 1 if y_true[i] > y_true[j] else -1
            pred_sign = 1 if y_pred[i] > y_pred[j] else -1
            if true_sign == pred_sign:
                correct += 1
    return float(correct) / float(total) if total else 0.0


def evaluate(df: pd.DataFrame, pred_col: str) -> Dict[str, object]:
    y_true = df["kill_ratio"].to_numpy(dtype=float)
    y_pred = df[pred_col].to_numpy(dtype=float)

    group_rows: List[Dict[str, object]] = []
    oracle_rows: List[Dict[str, object]] = []
    for group_id, g in df.groupby("group_id", sort=False):
        gt = g["kill_ratio"].to_numpy(dtype=float)
        pr = g[pred_col].to_numpy(dtype=float)
        best_true_idx = int(np.argmax(gt))
        best_pred_idx = int(np.argmax(pr))
        group_rows.append(
            {
                "group_id": group_id,
                "best_true": float(gt[best_true_idx]),
                "best_pred_true": float(gt[best_pred_idx]),
                "regret": float(np.max(gt) - gt[best_pred_idx]),
                "top1_hit": int(best_true_idx == best_pred_idx),
                "spearman": _spearman(gt, pr),
                "pairwise_acc": _pairwise_accuracy(gt, pr),
            }
        )
        oracle_rows.append(
            {
                "group_id": group_id,
                "best_bug_type": g.iloc[best_true_idx]["bug_type"],
                "best_true": float(gt[best_true_idx]),
                "best_pred_true": float(gt[best_pred_idx]),
                "regret": float(np.max(gt) - gt[best_pred_idx]),
                "top1_hit": int(best_true_idx == best_pred_idx),
            }
        )

    overall = {
        "mae": float(np.mean(np.abs(y_true - y_pred))),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        "spearman": _spearman(y_true, y_pred),
        "pairwise_acc": _pairwise_accuracy(y_true, y_pred),
        "group_spearman": float(np.mean([r["spearman"] for r in group_rows])) if group_rows else 0.0,
        "group_pairwise_acc": float(np.mean([r["pairwise_acc"] for r in group_rows])) if group_rows else 0.0,
        "group_top1_hit": float(np.mean([r["top1_hit"] for r in group_rows])) if group_rows else 0.0,
        "group_regret": float(np.mean([r["regret"] for r in group_rows])) if group_rows else 0.0,
    }

    oracle_df = pd.DataFrame(oracle_rows)
    bug_rows: List[Dict[str, object]] = []
    for bug_type, g in oracle_df.groupby("best_bug_type", sort=False):
        bug_rows.append(
            {
                "bug_type": bug_type,
                "groups": int(len(g)),
                "mean_best_true": float(g["best_true"].mean()),
                "mean_best_pred_true": float(g["best_pred_true"].mean()),
                "mean_regret": float(g["regret"].mean()),
                "top1_hit": float(g["top1_hit"].mean()),
            }
        )
    by_bug = pd.DataFrame(bug_rows)
    if not by_bug.empty:
        by_bug = by_bug.sort_values(["mean_regret", "top1_hit"], ascending=[False, True])

    return {
        "overall": overall,
        "oracle_by_bug_type": by_bug.to_dict(orient="records"),
        "oracle_rows": oracle_rows,
    }


def read_predictions(v2_path: str, v3_path: str) -> pd.DataFrame:
    v2 = pd.read_csv(v2_path)
    v3 = pd.read_csv(v3_path)
    key_cols = ["group_id", "candidate_index", "assertion"]
    keep = key_cols + ["prediction"]
    merged = v2.merge(
        v3[keep].rename(columns={"prediction": "prediction_v3"}),
        on=key_cols,
        how="inner",
    )
    merged = merged.rename(columns={"prediction": "prediction_v2"})
    return merged


def tune_global_blend(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for weight_v3 in np.linspace(0.0, 1.0, 21):
        col = f"blend_{weight_v3:.2f}"
        df[col] = (1.0 - weight_v3) * df["prediction_v2"] + weight_v3 * df["prediction_v3"]
        metrics = evaluate(df, col)["overall"]
        rows.append({"weight_v3": float(weight_v3), **metrics})
    return pd.DataFrame(rows)


def tune_bug_type_blend(df: pd.DataFrame, grid: np.ndarray) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    for bug_type, idx in df.groupby("bug_type").groups.items():
        best_weight = 0.0
        best_key = None
        subset = df.loc[idx].copy()
        for weight_v3 in grid:
            subset["tmp"] = (1.0 - weight_v3) * subset["prediction_v2"] + weight_v3 * subset["prediction_v3"]
            metrics = evaluate(subset, "tmp")["overall"]
            key = (
                -metrics["group_regret"],
                metrics["group_top1_hit"],
                metrics["group_spearman"],
                metrics["spearman"],
            )
            if best_key is None or key > best_key:
                best_key = key
                best_weight = float(weight_v3)
        weights[str(bug_type)] = best_weight
    return weights


def main() -> None:
    parser = argparse.ArgumentParser(description="Blend v2/v3 static reward baselines.")
    parser.add_argument("--v2", default=DEFAULT_V2)
    parser.add_argument("--v3", default=DEFAULT_V3)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    df = read_predictions(args.v2, args.v3)

    global_sweep = tune_global_blend(df)
    best_global = global_sweep.sort_values(
        ["group_regret", "group_top1_hit", "spearman"],
        ascending=[True, False, False],
    ).iloc[0]
    best_global_weight = float(best_global["weight_v3"])
    df["prediction_global_blend"] = (
        (1.0 - best_global_weight) * df["prediction_v2"]
        + best_global_weight * df["prediction_v3"]
    )

    bug_weights = tune_bug_type_blend(df, np.linspace(0.0, 1.0, 21))
    df["prediction_bug_blend"] = [
        (1.0 - bug_weights.get(str(row.bug_type), best_global_weight)) * row.prediction_v2
        + bug_weights.get(str(row.bug_type), best_global_weight) * row.prediction_v3
        for row in df.itertuples(index=False)
    ]

    reports = {
        "v2": evaluate(df, "prediction_v2"),
        "v3": evaluate(df, "prediction_v3"),
        "global_blend": evaluate(df, "prediction_global_blend"),
        "bug_type_blend": evaluate(df, "prediction_bug_blend"),
        "best_global_weight_v3": best_global_weight,
        "bug_type_weights_v3": bug_weights,
        "note": "Bug-type weights are an optimistic offline diagnostic because bug_type is known from this dataset.",
    }

    print("Global blend sweep:")
    print(global_sweep.to_string(index=False))
    print("\nSelected global v3 weight:", best_global_weight)
    print("\nBug-type v3 weights:")
    print(json.dumps(bug_weights, indent=2, ensure_ascii=False))
    print("\nOverall comparison:")
    for name in ["v2", "v3", "global_blend", "bug_type_blend"]:
        print(name, json.dumps(reports[name]["overall"], ensure_ascii=False))
    print("\nBug-type blend oracle regret by bug_type:")
    print(pd.DataFrame(reports["bug_type_blend"]["oracle_by_bug_type"]).to_string(index=False))

    df.to_csv(os.path.join(args.output_dir, "blend_predictions.csv"), index=False)
    global_sweep.to_csv(os.path.join(args.output_dir, "global_blend_sweep.csv"), index=False)
    with open(os.path.join(args.output_dir, "blend_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
