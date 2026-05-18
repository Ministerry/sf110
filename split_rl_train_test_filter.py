#!/usr/bin/env python3
import argparse
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


DEFAULT_CANDIDATES = [
    "rl_train_test_filter.parquet",
    "rl_train_test_filter.jsonl",
    "rl_train_test_filter.json",
    "rl_train_test_filter.csv",
    "rl_train_test_filter.tsv",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split rl_train_test_filter into train/val/test with an 8:1:1 ratio."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Input dataset path. If omitted, common rl_train_test_filter filenames are searched.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/self_rl_data"),
        help="Directory to write train/val/test files into.",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Train split ratio.",
    )
    parser.add_argument(
        "--val-ratio",
        type=float,
        default=0.1,
        help="Validation split ratio.",
    )
    parser.add_argument(
        "--test-ratio",
        type=float,
        default=0.1,
        help="Test split ratio.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used for shuffling.",
    )
    parser.add_argument(
        "--group-by",
        nargs="*",
        default=None,
        help=(
            "Columns used as a grouping key before splitting. "
            "Defaults to focal+prefix when available, otherwise focal, prefix, or row-level split."
        ),
    )
    return parser.parse_args()


def detect_input_path(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        if not explicit_path.exists():
            raise FileNotFoundError(f"Input file not found: {explicit_path}")
        return explicit_path

    search_roots = [Path.cwd(), Path.cwd() / "data", Path.cwd() / "data/self_rl_data"]
    for root in search_roots:
        for candidate in DEFAULT_CANDIDATES:
            path = root / candidate
            if path.exists():
                return path

    raise FileNotFoundError(
        "Could not find rl_train_test_filter automatically. "
        "Please pass --input /path/to/rl_train_test_filter.xxx"
    )


def load_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".jsonl":
        return pd.read_json(path, orient="records", lines=True)
    if suffix == ".json":
        return pd.read_json(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t")
    raise ValueError(f"Unsupported input format: {path.suffix}")


def infer_group_columns(df: pd.DataFrame, requested: Iterable[str] | None) -> list[str]:
    if requested:
        cols = [col for col in requested if col in df.columns]
        if not cols:
            raise ValueError(f"None of the requested group-by columns exist: {requested}")
        return cols

    if {"focal", "prefix"}.issubset(df.columns):
        return ["focal", "prefix"]
    if "focal" in df.columns:
        return ["focal"]
    if "prefix" in df.columns:
        return ["prefix"]
    return []


def build_group_ids(df: pd.DataFrame, group_cols: list[str]) -> pd.Series:
    if not group_cols:
        return pd.Series([f"row_{i}" for i in range(len(df))], index=df.index)

    key_df = df[group_cols].fillna("<NA>").astype(str)
    return key_df.agg("||".join, axis=1)


def compute_split_sizes(total: int, train_ratio: float, val_ratio: float, test_ratio: float) -> tuple[int, int, int]:
    ratio_sum = train_ratio + val_ratio + test_ratio
    if not math.isclose(ratio_sum, 1.0, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError(f"Ratios must sum to 1.0, got {ratio_sum}")

    train_size = int(total * train_ratio)
    val_size = int(total * val_ratio)
    test_size = total - train_size - val_size
    return train_size, val_size, test_size


def split_groups(unique_groups: pd.Index, train_ratio: float, val_ratio: float, test_ratio: float, seed: int):
    shuffled = unique_groups.to_series().sample(frac=1.0, random_state=seed).tolist()
    train_size, val_size, _ = compute_split_sizes(len(shuffled), train_ratio, val_ratio, test_ratio)

    train_groups = set(shuffled[:train_size])
    val_groups = set(shuffled[train_size : train_size + val_size])
    test_groups = set(shuffled[train_size + val_size :])
    return train_groups, val_groups, test_groups


def write_dataframe(df: pd.DataFrame, path: Path) -> None:
    df.to_json(path, orient="records", force_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    input_path = detect_input_path(args.input)
    df = load_dataframe(input_path)
    group_cols = infer_group_columns(df, args.group_by)
    group_ids = build_group_ids(df, group_cols)

    train_groups, val_groups, test_groups = split_groups(
        pd.Index(group_ids.unique()),
        args.train_ratio,
        args.val_ratio,
        args.test_ratio,
        args.seed,
    )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    train_df = df[group_ids.isin(train_groups)].reset_index(drop=True)
    val_df = df[group_ids.isin(val_groups)].reset_index(drop=True)
    test_df = df[group_ids.isin(test_groups)].reset_index(drop=True)

    write_dataframe(train_df, output_dir / "train.json")
    write_dataframe(val_df, output_dir / "val.json")
    write_dataframe(test_df, output_dir / "test.json")

    print(f"Input: {input_path}")
    print(f"Grouping columns: {group_cols if group_cols else 'row-level'}")
    print(f"Train: {len(train_df)} rows, {len(train_groups)} groups")
    print(f"Val:   {len(val_df)} rows, {len(val_groups)} groups")
    print(f"Test:  {len(test_df)} rows, {len(test_groups)} groups")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
