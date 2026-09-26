"""
Phase 2 - Step 2: Explore Data (EDA)

Performs comprehensive exploratory data analysis on the raw/intermediate dataset.

EDA includes:
1. Dataset overview
2. Data type analysis
3. Missing value analysis
4. Descriptive statistics
5. Flight-level structure
6. Time-series structure
7. Flights per date
8. Flights per route
9. Feature distributions
10. Feature correlation analysis
11. Basic data integrity summary

Important:
- This script DOES NOT clean data.
- This script DOES NOT impute missing values.
- This script DOES NOT remove duplicates.
- This script DOES NOT modify the input dataset.
- All outputs are saved under output/results/data_quality
  and output/figures/data_quality.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

from config.paths import (
    DATA_INTERMEDIATE_DIR,
    OUT_RES_QUALITY,
    OUT_FIG_QUALITY,
)

from config.dataset_config import ALL_FEATURES

from src.utils.logging_utils import get_logger


logger = get_logger("ExploreData")


# ============================================================
# Utility
# ============================================================

def ensure_output_dirs():
    """Create output directories if they do not exist."""
    OUT_RES_QUALITY.mkdir(parents=True, exist_ok=True)
    OUT_FIG_QUALITY.mkdir(parents=True, exist_ok=True)


# ============================================================
# 1. Dataset Overview
# ============================================================

def analyze_dataset_overview(df):
    """Generate high-level dataset overview."""

    logger.info("Analyzing dataset overview...")

    overview = {
        "Total Rows": len(df),
        "Total Columns": len(df.columns),
        "Unique Flights": (
            df["flight"].nunique()
            if "flight" in df.columns
            else np.nan
        ),
        "Unique Dates": (
            df["date"].nunique()
            if "date" in df.columns
            else np.nan
        ),
        "Unique Routes": (
            df["route"].nunique()
            if "route" in df.columns
            else np.nan
        ),
        "Exact Duplicate Rows": df.duplicated().sum(),
    }

    overview_df = pd.DataFrame([overview])

    output_path = OUT_RES_QUALITY / "dataset_overview.csv"
    overview_df.to_csv(output_path, index=False)

    logger.info(
        f"Rows: {overview['Total Rows']:,}, "
        f"Columns: {overview['Total Columns']}, "
        f"Flights: {overview['Unique Flights']}, "
        f"Dates: {overview['Unique Dates']}, "
        f"Routes: {overview['Unique Routes']}"
    )

    logger.info(
        f"Saved dataset overview to {output_path}"
    )


# ============================================================
# 2. Data Types
# ============================================================

def analyze_data_types(df):
    """Analyze column data types and basic structure."""

    logger.info("Analyzing data types...")

    dtype_df = pd.DataFrame({
        "Column": df.columns,
        "Data Type": df.dtypes.astype(str).values,
        "Non-Null Count": df.notna().sum().values,
        "Null Count": df.isna().sum().values,
        "Unique Values": [
            df[col].nunique(dropna=True)
            for col in df.columns
        ],
    })

    output_path = OUT_RES_QUALITY / "data_types_summary.csv"
    dtype_df.to_csv(output_path, index=False)

    logger.info(
        f"Saved data type summary to {output_path}"
    )


# ============================================================
# 3. Missing Value Analysis
# ============================================================

def analyze_missing_values(df):
    """
    Analyze missing values for every column.

    This is EDA only:
    no imputation or deletion is performed.
    """

    logger.info("Analyzing missing values...")

    missing_count = df.isna().sum()

    missing_df = pd.DataFrame({
        "Column": df.columns,
        "Missing Count": missing_count.values,
        "Missing Percentage": (
            missing_count.values / len(df) * 100
        ),
        "Non-Missing Count": (
            len(df) - missing_count.values
        ),
    })

    missing_df = missing_df.sort_values(
        "Missing Count",
        ascending=False
    )

    output_path = OUT_RES_QUALITY / "missing_values_report.csv"
    missing_df.to_csv(output_path, index=False)

    total_missing_cells = int(missing_count.sum())

    logger.info(
        f"Total missing cells: {total_missing_cells:,}"
    )

    columns_with_missing = missing_df[
        missing_df["Missing Count"] > 0
    ]

    logger.info(
        f"Columns containing missing values: "
        f"{len(columns_with_missing)}"
    )

    for _, row in columns_with_missing.iterrows():
        logger.info(
            f"  {row['Column']}: "
            f"{int(row['Missing Count']):,} "
            f"({row['Missing Percentage']:.2f}%)"
        )

    # Missing-value bar chart
    if not columns_with_missing.empty:

        plt.figure(figsize=(12, 6))

        plt.bar(
            columns_with_missing["Column"],
            columns_with_missing["Missing Percentage"]
        )

        plt.xticks(
            rotation=90,
            ha="right"
        )

        plt.ylabel("Missing Percentage (%)")
        plt.xlabel("Feature")
        plt.title("Missing Values by Feature")

        plt.tight_layout()

        figure_path = (
            OUT_FIG_QUALITY
            / "missing_values.png"
        )

        plt.savefig(
            figure_path,
            dpi=150,
            bbox_inches="tight"
        )

        plt.close()

    logger.info(
        f"Saved missing-value report to {output_path}"
    )


# ============================================================
# 4. Descriptive Statistics
# ============================================================

def analyze_descriptive_statistics(df):
    """Generate descriptive statistics for numerical features."""

    logger.info("Analyzing descriptive statistics...")

    numeric_df = df.select_dtypes(
        include=[np.number]
    )

    if numeric_df.empty:
        logger.warning(
            "No numerical columns found."
        )
        return

    stats = numeric_df.describe().T

    stats["missing_count"] = numeric_df.isna().sum()

    stats["missing_percentage"] = (
        numeric_df.isna().mean() * 100
    )

    stats["unique_values"] = [
        numeric_df[col].nunique(dropna=True)
        for col in numeric_df.columns
    ]

    output_path = (
        OUT_RES_QUALITY
        / "descriptive_statistics.csv"
    )

    stats.to_csv(output_path)

    logger.info(
        f"Saved descriptive statistics to {output_path}"
    )


# ============================================================
# 5. Flight-level Structure
# ============================================================

def analyze_flight_structure(df):
    """
    Analyze number of observations and duration for each flight.
    """

    logger.info("Analyzing flight-level structure...")

    if "flight" not in df.columns:
        logger.warning(
            "Column 'flight' not found. "
            "Skipping flight-level analysis."
        )
        return

    grouped = df.groupby("flight")

    flight_stats = grouped.size().rename(
        "rows_per_flight"
    ).to_frame()

    # Unique timestamps
    if "time" in df.columns:

        unique_times = grouped["time"].nunique()

        flight_stats["unique_time_points"] = (
            unique_times
        )

        flight_stats["duplicate_time_rows"] = (
            flight_stats["rows_per_flight"]
            - flight_stats["unique_time_points"]
        )

        flight_stats["start_time"] = grouped["time"].min()

        flight_stats["end_time"] = grouped["time"].max()

        flight_stats["duration_seconds"] = (
            flight_stats["end_time"]
            - flight_stats["start_time"]
        )

    # Date
    if "date" in df.columns:
        flight_stats["date"] = grouped["date"].first()

    # Route
    if "route" in df.columns:
        flight_stats["route"] = grouped["route"].first()

    flight_stats = flight_stats.reset_index()

    output_path = (
        OUT_RES_QUALITY
        / "flight_level_structure.csv"
    )

    flight_stats.to_csv(
        output_path,
        index=False
    )

    logger.info(
        f"Analyzed {len(flight_stats)} flights."
    )

    # Summary statistics
    summary_columns = [
        c for c in [
            "rows_per_flight",
            "unique_time_points",
            "duration_seconds",
        ]
        if c in flight_stats.columns
    ]

    if summary_columns:

        summary = (
            flight_stats[summary_columns]
            .describe()
            .T
        )

        summary.to_csv(
            OUT_RES_QUALITY
            / "flight_level_summary.csv"
        )

    # Rows per flight distribution
    plt.figure(figsize=(10, 6))

    plt.hist(
        flight_stats["rows_per_flight"],
        bins=30
    )

    plt.xlabel("Rows per Flight")
    plt.ylabel("Number of Flights")
    plt.title("Distribution of Rows per Flight")

    plt.tight_layout()

    plt.savefig(
        OUT_FIG_QUALITY
        / "rows_per_flight_distribution.png",
        dpi=150
    )

    plt.close()

    # Flight duration distribution
    if "duration_seconds" in flight_stats.columns:

        plt.figure(figsize=(10, 6))

        plt.hist(
            flight_stats["duration_seconds"],
            bins=30
        )

        plt.xlabel("Flight Duration (seconds)")
        plt.ylabel("Number of Flights")
        plt.title("Distribution of Flight Duration")

        plt.tight_layout()

        plt.savefig(
            OUT_FIG_QUALITY
            / "flight_duration_distribution.png",
            dpi=150
        )

        plt.close()


# ============================================================
# 6. Time-Series Structure
# ============================================================

def analyze_time_series(df):
    """
    Analyze telemetry time structure.

    Sampling interval is calculated within each flight,
    never across different flights.
    """

    logger.info("Analyzing time-series structure...")

    if "flight" not in df.columns:
        logger.warning(
            "Column 'flight' not found. "
            "Skipping time-series analysis."
        )
        return

    if "time" not in df.columns:
        logger.warning(
            "Column 'time' not found. "
            "Skipping time-series analysis."
        )
        return

    # Sort only a copy; original df is not modified.
    temp = df[
        ["flight", "time"]
    ].dropna().copy()

    temp = temp.sort_values(
        ["flight", "time"]
    )

    # Difference within each flight
    temp["sampling_interval_seconds"] = (
        temp.groupby("flight")["time"].diff()
    )

    intervals = temp[
        "sampling_interval_seconds"
    ].dropna()

    if intervals.empty:
        logger.warning(
            "No valid sampling intervals found."
        )
        return

    interval_stats = pd.DataFrame({
        "Metric": [
            "count",
            "mean_seconds",
            "std_seconds",
            "min_seconds",
            "25_percentile_seconds",
            "median_seconds",
            "75_percentile_seconds",
            "max_seconds",
        ],
        "Value": [
            len(intervals),
            intervals.mean(),
            intervals.std(),
            intervals.min(),
            intervals.quantile(0.25),
            intervals.median(),
            intervals.quantile(0.75),
            intervals.max(),
        ],
    })

    output_path = (
        OUT_RES_QUALITY
        / "sampling_interval_statistics.csv"
    )

    interval_stats.to_csv(
        output_path,
        index=False
    )

    logger.info(
        "Sampling interval statistics: "
        f"median={intervals.median():.6f}s, "
        f"mean={intervals.mean():.6f}s"
    )

    # Time range per flight
    time_range = (
        temp.groupby("flight")["time"]
        .agg(
            start_time="min",
            end_time="max"
        )
        .reset_index()
    )

    time_range["duration_seconds"] = (
        time_range["end_time"]
        - time_range["start_time"]
    )

    time_range.to_csv(
        OUT_RES_QUALITY
        / "flight_time_ranges.csv",
        index=False
    )

    # Sampling interval distribution
    plt.figure(figsize=(10, 6))

    # Limit extreme tail only for visualization.
    upper = intervals.quantile(0.99)

    plot_intervals = intervals[
        intervals <= upper
    ]

    plt.hist(
        plot_intervals,
        bins=50
    )

    plt.xlabel("Sampling Interval (seconds)")
    plt.ylabel("Frequency")
    plt.title(
        "Sampling Interval Distribution "
        "(up to 99th percentile)"
    )

    plt.tight_layout()

    plt.savefig(
        OUT_FIG_QUALITY
        / "sampling_interval_distribution.png",
        dpi=150
    )

    plt.close()


# ============================================================
# 7. Flights per Date
# ============================================================

def analyze_flights_per_date(df):
    """Analyze number of flights per date."""

    logger.info("Analyzing flights per date...")

    if "date" not in df.columns:
        logger.warning(
            "Column 'date' not found."
        )
        return

    if "flight" not in df.columns:
        logger.warning(
            "Column 'flight' not found."
        )
        return

    flights_per_date = (
        df.groupby("date")["flight"]
        .nunique()
        .reset_index(
            name="flight_count"
        )
        .sort_values("date")
    )

    output_path = (
        OUT_RES_QUALITY
        / "flights_per_date.csv"
    )

    flights_per_date.to_csv(
        output_path,
        index=False
    )

    plt.figure(figsize=(12, 6))

    plt.bar(
        flights_per_date["date"].astype(str),
        flights_per_date["flight_count"]
    )

    plt.xticks(
        rotation=45,
        ha="right"
    )

    plt.xlabel("Date")
    plt.ylabel("Number of Flights")
    plt.title("Number of Flights per Date")

    plt.tight_layout()

    plt.savefig(
        OUT_FIG_QUALITY
        / "flights_per_date.png",
        dpi=150
    )

    plt.close()


# ============================================================
# 8. Flights per Route
# ============================================================

def analyze_flights_per_route(df):
    """Analyze number of flights per route."""

    logger.info("Analyzing flights per route...")

    if "route" not in df.columns:
        logger.warning(
            "Column 'route' not found."
        )
        return

    if "flight" not in df.columns:
        logger.warning(
            "Column 'flight' not found."
        )
        return

    flights_per_route = (
        df.groupby("route")["flight"]
        .nunique()
        .reset_index(
            name="flight_count"
        )
        .sort_values(
            "flight_count",
            ascending=False
        )
    )

    output_path = (
        OUT_RES_QUALITY
        / "flights_per_route.csv"
    )

    flights_per_route.to_csv(
        output_path,
        index=False
    )

    plt.figure(figsize=(12, 6))

    plt.bar(
        flights_per_route["route"].astype(str),
        flights_per_route["flight_count"]
    )

    plt.xticks(
        rotation=45,
        ha="right"
    )

    plt.xlabel("Route")
    plt.ylabel("Number of Flights")
    plt.title("Number of Flights per Route")

    plt.tight_layout()

    plt.savefig(
        OUT_FIG_QUALITY
        / "flights_per_route.png",
        dpi=150
    )

    plt.close()


# ============================================================
# 9. Feature Distributions
# ============================================================

def analyze_feature_distributions(df):
    """
    Generate histograms for numerical model features.

    Uses ALL_FEATURES from dataset_config.py.
    """

    logger.info("Analyzing feature distributions...")

    features = [
        col
        for col in ALL_FEATURES
        if col in df.columns
        and pd.api.types.is_numeric_dtype(df[col])
    ]

    if not features:
        logger.warning(
            "No numerical features available "
            "for distribution analysis."
        )
        return

    generated = 0

    for col in features:

        values = df[col].dropna()

        if values.empty:
            continue

        plt.figure(figsize=(10, 6))

        plt.hist(
            values,
            bins=50
        )

        plt.xlabel(col)
        plt.ylabel("Frequency")
        plt.title(
            f"Distribution of {col}"
        )

        plt.tight_layout()

        safe_name = (
            col.replace("/", "_")
               .replace(" ", "_")
        )

        output_path = (
            OUT_FIG_QUALITY
            / f"distribution_{safe_name}.png"
        )

        plt.savefig(
            output_path,
            dpi=150
        )

        plt.close()

        generated += 1

    logger.info(
        f"Generated distributions for {generated} features."
    )


# ============================================================
# 10. Correlation Analysis
# ============================================================

def analyze_correlations(df):
    """
    Generate correlation matrix for numerical model features.

    Target is included automatically only if it exists.
    """

    logger.info("Analyzing feature correlations...")

    features = [
        col
        for col in ALL_FEATURES
        if col in df.columns
        and pd.api.types.is_numeric_dtype(df[col])
    ]

    # Optional target
    target = "energy_efficiency"

    if target in df.columns:
        if pd.api.types.is_numeric_dtype(
            df[target]
        ):
            if target not in features:
                features.append(target)

    if not features:
        logger.warning(
            "No numerical features available "
            "for correlation analysis."
        )
        return

    corr = df[features].corr()

    output_path = (
        OUT_RES_QUALITY
        / "correlation_matrix.csv"
    )

    corr.to_csv(output_path)

    # Correlation matrix figure
    # Use matplotlib only.
    fig_size = max(
        10,
        min(20, len(features) * 0.6)
    )

    plt.figure(
        figsize=(fig_size, fig_size)
    )

    plt.imshow(
        corr,
        aspect="auto",
        vmin=-1,
        vmax=1
    )

    plt.colorbar(
        label="Correlation"
    )

    plt.xticks(
        range(len(features)),
        features,
        rotation=90
    )

    plt.yticks(
        range(len(features)),
        features
    )

    plt.title(
        "Feature Correlation Matrix"
    )

    plt.tight_layout()

    figure_path = (
        OUT_FIG_QUALITY
        / "correlation_matrix.png"
    )

    plt.savefig(
        figure_path,
        dpi=150,
        bbox_inches="tight"
    )

    plt.close()

    logger.info(
        f"Correlation matrix generated "
        f"for {len(features)} numerical features."
    )

    if target in df.columns:
        logger.info(
            "Target 'energy_efficiency' "
            "included in correlation analysis."
        )
    else:
        logger.info(
            "Target 'energy_efficiency' is not "
            "present in this raw dataset; "
            "target correlation skipped."
        )


# ============================================================
# 11. Data Integrity Summary
# ============================================================

def analyze_integrity(df):
    """
    Produce a compact structural integrity summary.

    This does NOT modify the data.
    """

    logger.info("Analyzing data integrity summary...")

    summary = {}

    summary["rows"] = len(df)
    summary["columns"] = len(df.columns)

    summary["exact_duplicate_rows"] = (
        df.duplicated().sum()
    )

    if "flight" in df.columns:
        summary["unique_flights"] = (
            df["flight"].nunique()
        )

        summary["missing_flight_ids"] = (
            df["flight"].isna().sum()
        )

    if (
        "flight" in df.columns
        and "time" in df.columns
    ):

        summary["duplicate_flight_time"] = (
            df.duplicated(
                ["flight", "time"]
            ).sum()
        )

    if (
        "flight" in df.columns
        and "time" in df.columns
    ):

        summary["unique_flight_time"] = (
            df[
                ["flight", "time"]
            ].drop_duplicates().shape[0]
        )

    summary_df = pd.DataFrame([
        summary
    ])

    output_path = (
        OUT_RES_QUALITY
        / "data_integrity_summary.csv"
    )

    summary_df.to_csv(
        output_path,
        index=False
    )

    logger.info(
        f"Exact duplicate rows: "
        f"{summary.get('exact_duplicate_rows', 'N/A')}"
    )

    if "duplicate_flight_time" in summary:
        logger.info(
            "Duplicate (flight, time): "
            f"{summary['duplicate_flight_time']}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("=" * 60)
    logger.info("START: Explore Data (EDA)")

    ensure_output_dirs()

    input_path = (
        DATA_INTERMEDIATE_DIR
        / "flight_with_weather.csv"
    )

    if not input_path.exists():

        logger.error(
            f"Input file not found: {input_path}"
        )

        return

    # --------------------------------------------------------
    # Load data
    # --------------------------------------------------------

    df = pd.read_csv(input_path)

    logger.info(
        f"Loaded dataset: "
        f"{df.shape[0]:,} rows, "
        f"{df.shape[1]} columns"
    )

    # --------------------------------------------------------
    # EDA analyses
    # --------------------------------------------------------

    analyze_dataset_overview(df)

    analyze_data_types(df)

    analyze_missing_values(df)

    analyze_descriptive_statistics(df)

    analyze_flight_structure(df)

    analyze_time_series(df)

    analyze_flights_per_date(df)

    analyze_flights_per_route(df)

    analyze_feature_distributions(df)

    analyze_correlations(df)

    analyze_integrity(df)

    # --------------------------------------------------------
    # Finish
    # --------------------------------------------------------

    logger.info(
        "EDA completed successfully."
    )

    logger.info(
        f"Results saved to: "
        f"{OUT_RES_QUALITY}"
    )

    logger.info(
        f"Figures saved to: "
        f"{OUT_FIG_QUALITY}"
    )

    logger.info(
        "END: Explore Data (EDA)"
    )

    logger.info("=" * 60)


if __name__ == "__main__":
    main()