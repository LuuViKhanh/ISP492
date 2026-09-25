"""Backward-compatible entry point for segment Energy modeling.

The former ``decisions/segment_model_decision.yaml`` approval gate is retired.
Calling this module without a selected model benchmarks candidates and stops at
the segment-model checkpoint. Supplying ``--model`` fits that explicit choice.
"""

from __future__ import annotations

import argparse

from config.multiuav_config import MODEL_BENCHMARK_CANDIDATES
from src.multihub.segment_modeling import (
    benchmark_segment_models,
    fit_selected_segment_model,
)


def train_segment_energy_model(df=None, selected_model: str | None = None):
    # df is kept only for compatibility with older callers; the canonical core
    # reads the audited G06 CSV so the evidence and fitting commands use exactly
    # the same persisted dataset.
    if selected_model is None:
        return benchmark_segment_models("all")
    return fit_selected_segment_model(selected_model)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=MODEL_BENCHMARK_CANDIDATES, default=None)
    parser.add_argument("--models", default="all")
    args = parser.parse_args()
    if args.model:
        fit_selected_segment_model(args.model)
    else:
        benchmark_segment_models(args.models)


if __name__ == "__main__":
    main()
