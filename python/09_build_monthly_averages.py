import calendar
import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from FaaSr_py.client.py_client_stubs import (
    faasr_get_file,
    faasr_get_folder_list,
    faasr_log,
    faasr_put_file,
)

CONFIG_CODES = [
    "C_O_N", "C_O_D", "C_O_F",
    "S_W_N", "S_C_N", "S_E_N",
    "S_W_D", "S_C_D", "S_E_D",
    "S_W_F", "S_C_F", "S_E_F",
]

MONTHLY_OUTPUT_COLUMNS = [
    "Location_code",
    "Shade_zone_code",
    "Irrigation_code",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "measurement",
    "date_time",
    "Value",
    "Units",
]

GROUP_BASE = [
    "Location_code",
    "Shade_zone_code",
    "Irrigation_code",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "measurement",
    "Units",
]


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _exists(folder: str, filename: str) -> bool:
    folder = folder.strip().rstrip("/")
    key = f"{folder}/{filename}" if folder else filename
    try:
        objs = _normalize_list_result(
            faasr_get_folder_list(prefix=key)
        )
    except Exception:
        return False
    return any(
        str(x).strip().lstrip("/") == key
        or str(x).strip().endswith(f"/{filename}")
        or str(x).strip() == filename
        for x in objs
    )


def _download_csv(folder: str, filename: str, local: str) -> pd.DataFrame:
    faasr_get_file(
        local_file=local,
        remote_folder=folder,
        remote_file=filename,
    )
    return pd.read_csv(local)


def _download_json(folder: str, filename: str, local: str) -> dict:
    faasr_get_file(
        local_file=local,
        remote_folder=folder,
        remote_file=filename,
    )
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f)


def _put_json(obj: dict, folder: str, filename: str) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    faasr_put_file(
        local_file=filename,
        remote_folder=folder,
        remote_file=filename,
    )


def _is_error_flag(value: Any) -> bool:
    if pd.isna(value):
        return False
    s = str(value).strip().lower()
    return s not in {"", "0", "0.0", "false", "none", "nan", "no"}


def _standardize(
    df: pd.DataFrame,
    timezone_name: str,
    exclude_error_rows: bool,
) -> tuple[pd.DataFrame, dict]:
    work = df.copy()

    rename = {
        "location_code": "Location_code",
        "shade_zone_code": "Shade_zone_code",
        "irrigation_code": "Irrigation_code",
        "units": "Units",
        "value": "Value",
        "Measurement": "measurement",
    }
    work = work.rename(
        columns={k: v for k, v in rename.items() if k in work.columns}
    )

    required = [
        "Location_code",
        "Shade_zone_code",
        "Irrigation_code",
        "logger_name",
        "logger_serial_number",
        "port_num",
        "port_description",
        "measurement",
        "datetime",
        "Value",
        "Units",
    ]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(
            f"Required columns missing from final config CSV: {missing}"
        )

    input_rows = len(work)
    work["Value"] = pd.to_numeric(work["Value"], errors="coerce")
    work["port_num"] = pd.to_numeric(
        work["port_num"], errors="coerce"
    ).astype("Int64")

    # Normalize fields used in duplicate comparison.  The final 12-config
    # files intentionally preserve overlapping source rows, so monthly
    # processing removes a row ONLY when the actual observation contents
    # match, including Value and Units.
    work["logger_serial_number"] = (
        work["logger_serial_number"].astype("string").str.strip()
    )
    work["measurement"] = (
        work["measurement"].astype("string").str.strip()
    )
    work["Units"] = work["Units"].astype("string").str.strip()

    if "sensor_sn" in work.columns:
        work["sensor_sn"] = work["sensor_sn"].astype("string").str.strip()
    if "mrid" in work.columns:
        # Normalize 95294, 95294.0 and "95294" to the same comparable form.
        mrid_num = pd.to_numeric(work["mrid"], errors="coerce")
        work["_mrid_key"] = work["mrid"].astype("string").str.strip()
        integer_mask = mrid_num.notna() & ((mrid_num % 1) == 0)
        work.loc[integer_mask, "_mrid_key"] = (
            mrid_num.loc[integer_mask].astype("Int64").astype("string")
        )
    if "sub_sensor_index" in work.columns:
        work["sub_sensor_index"] = pd.to_numeric(
            work["sub_sensor_index"], errors="coerce"
        ).astype("Int64")
    if "timestamp_utc" in work.columns:
        work["timestamp_utc"] = pd.to_numeric(
            work["timestamp_utc"], errors="coerce"
        )

    work["_dt_utc"] = pd.to_datetime(
        work["datetime"], errors="coerce", utc=True
    )

    invalid_mask = work["Value"].isna() | work["_dt_utc"].isna()
    invalid_rows = int(invalid_mask.sum())
    work = work[~invalid_mask].copy()

    error_rows_removed = 0
    if exclude_error_rows and "error_flag" in work.columns:
        err = work["error_flag"].map(_is_error_flag)
        error_rows_removed = int(err.sum())
        work = work[~err].copy()

    work["_local_dt"] = work["_dt_utc"].dt.tz_convert(timezone_name)
    work["_local_date"] = work["_local_dt"].dt.date
    work["_month_key"] = work["_local_dt"].dt.strftime("%Y-%m")

    qa = {
        "input_rows": int(input_rows),
        "invalid_value_or_datetime_rows_removed": invalid_rows,
        "error_rows_removed": error_rows_removed,
    }
    return work, qa


def _event_identity_columns(
    df: pd.DataFrame,
    include_value_and_units: bool = True,
) -> list[str]:
    """
    Columns used to decide whether two rows are duplicates.

    IMPORTANT:
    source_file is deliberately excluded because the same Zentra observation
    can legitimately appear in multiple overlapping raw source files.

    A row is removed as a duplicate ONLY when all available observation
    identity fields match.  In exact-duplicate mode, Value and Units MUST also
    match. Therefore:
      - same sensor/time but different measurement -> both kept
      - same sensor/time/measurement but different Value -> both kept
      - same sensor/time/measurement/value but different Units -> both kept
      - same observation including Value + Units, different source_file -> one kept
    """
    candidates = [
        "logger_serial_number",
        "port_num",
        "_mrid_key",
        "timestamp_utc",
        "_dt_utc",
        "measurement",
        "sub_sensor_index",
        "sensor_sn",
    ]

    if include_value_and_units:
        candidates.extend(["Value", "Units"])

    cols = [c for c in candidates if c in df.columns]

    if "logger_serial_number" not in cols or "port_num" not in cols:
        raise ValueError("Cannot construct logical event identity.")
    if "measurement" not in cols:
        raise ValueError("Cannot construct duplicate identity without measurement.")
    if include_value_and_units:
        if "Value" not in cols or "Units" not in cols:
            raise ValueError(
                "Exact duplicate comparison requires both Value and Units."
            )

    return cols


def _dedupe_events(
    df: pd.DataFrame,
    conflict_policy: str = "keep_distinct",
) -> tuple[pd.DataFrame, dict]:
    """
    Remove ONLY exact duplicate observations.

    The old implementation treated rows sharing the same sensor-event identity
    but having different Values as a conflict and could stop the workflow.
    That is no longer done.

    Value and Units are now part of the duplicate definition. If either differs,
    the rows are considered distinct observations and BOTH are retained.

    conflict_policy is retained only for compatibility with older FaaSr JSONs.
    Values such as "fail" or "exclude" no longer cause distinct-value rows to
    be removed or to fail the workflow.
    """
    if df.empty:
        return df.copy(), {
            "duplicate_definition": (
                "all available event fields + measurement + Value + Units; "
                "source_file excluded"
            ),
            "duplicate_rows_removed": 0,
            "exact_duplicate_groups": 0,
            "same_base_event_different_value_groups_kept": 0,
            "same_base_event_different_units_groups_kept": 0,
            "conflicting_event_groups": 0,
        }

    policy = str(conflict_policy).strip().lower()
    if policy in {"fail", "exclude"}:
        faasr_log(
            f"Legacy conflict_policy='{policy}' received. "
            "Exact-duplicate mode keeps rows with different Value or Units; "
            "the legacy conflict action is not applied."
        )

    work = df.copy()

    # Base identity is useful only for QA: it tells us how many same-sensor/time
    # groups contain multiple values or multiple units.  Those rows are KEPT.
    base_identity = _event_identity_columns(
        work,
        include_value_and_units=False,
    )
    variant_summary = (
        work.groupby(base_identity, dropna=False)
        .agg(
            _distinct_values=("Value", lambda s: s.nunique(dropna=False)),
            _distinct_units=("Units", lambda s: s.nunique(dropna=False)),
        )
        .reset_index()
    )
    different_value_groups = int(
        (variant_summary["_distinct_values"] > 1).sum()
    )
    different_unit_groups = int(
        (variant_summary["_distinct_units"] > 1).sum()
    )

    # Exact duplicate identity includes BOTH Value and Units.
    exact_identity = _event_identity_columns(
        work,
        include_value_and_units=True,
    )

    duplicate_mask = work.duplicated(
        subset=exact_identity,
        keep=False,
    )
    if duplicate_mask.any():
        exact_duplicate_groups = int(
            work.loc[duplicate_mask]
            .groupby(exact_identity, dropna=False)
            .ngroups
        )
    else:
        exact_duplicate_groups = 0

    before = len(work)
    work = work.drop_duplicates(
        subset=exact_identity,
        keep="first",
    ).copy()
    duplicate_removed = before - len(work)

    return work, {
        "duplicate_definition": (
            "logger/port + available MRID/time/sensor fields + "
            "measurement + Value + Units; source_file excluded"
        ),
        "duplicate_rows_removed": int(duplicate_removed),
        "exact_duplicate_groups": int(exact_duplicate_groups),
        "same_base_event_different_value_groups_kept": (
            different_value_groups
        ),
        "same_base_event_different_units_groups_kept": (
            different_unit_groups
        ),
        # Kept for backward-compatible summaries. Distinct values are no longer
        # classified as conflicts under the user's requested definition.
        "conflicting_event_groups": 0,
        "legacy_conflict_policy_received": policy,
    }


def _month_expected_days(month_key: str) -> int:
    year, month = map(int, month_key.split("-"))
    return calendar.monthrange(year, month)[1]


def _build_monthly(
    df: pd.DataFrame,
    timezone_name: str,
    average_method: str,
    exclude_error_rows: bool,
    conflict_policy: str,
    minimum_day_coverage_ratio: float,
) -> tuple[pd.DataFrame, dict]:
    work, qa = _standardize(
        df,
        timezone_name=timezone_name,
        exclude_error_rows=exclude_error_rows,
    )
    work, dedupe_qa = _dedupe_events(
        work,
        conflict_policy=conflict_policy,
    )
    qa.update(dedupe_qa)

    if work.empty:
        qa.update({
            "clean_rows": 0,
            "monthly_rows": 0,
            "low_coverage_groups": 0,
        })
        return pd.DataFrame(columns=MONTHLY_OUTPUT_COLUMNS), qa

    average_method = str(average_method).strip().lower()
    if average_method not in {"daily_mean", "observation_mean"}:
        raise ValueError(
            "average_method must be daily_mean or observation_mean."
        )

    if average_method == "daily_mean":
        daily_group = GROUP_BASE + ["_month_key", "_local_date"]
        daily = (
            work.groupby(daily_group, dropna=False)
            .agg(Value=("Value", "mean"))
            .reset_index()
        )
        monthly = (
            daily.groupby(GROUP_BASE + ["_month_key"], dropna=False)
            .agg(
                Value=("Value", "mean"),
                _days_with_data=("_local_date", "nunique"),
                _last_date=("_local_date", "max"),
            )
            .reset_index()
        )
    else:
        monthly = (
            work.groupby(GROUP_BASE + ["_month_key"], dropna=False)
            .agg(
                Value=("Value", "mean"),
                _days_with_data=("_local_date", "nunique"),
                _last_date=("_local_date", "max"),
            )
            .reset_index()
        )

    monthly["_expected_days"] = monthly["_month_key"].map(
        _month_expected_days
    )
    monthly["_coverage_ratio"] = (
        monthly["_days_with_data"] / monthly["_expected_days"]
    )
    monthly["_low_coverage"] = (
        monthly["_coverage_ratio"] < float(minimum_day_coverage_ratio)
    )

    monthly["date_time"] = pd.to_datetime(
        monthly["_last_date"].astype(str),
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")

    out = monthly[MONTHLY_OUTPUT_COLUMNS].copy()
    out = out.sort_values(
        [
            "Location_code",
            "Shade_zone_code",
            "Irrigation_code",
            "logger_name",
            "logger_serial_number",
            "port_num",
            "measurement",
            "date_time",
            "Units",
        ]
    ).reset_index(drop=True)

    qa.update({
        "clean_rows": int(len(work)),
        "monthly_rows": int(len(out)),
        "low_coverage_groups": int(monthly["_low_coverage"].sum()),
        "coverage": monthly[
            GROUP_BASE
            + [
                "_month_key",
                "_days_with_data",
                "_expected_days",
                "_coverage_ratio",
                "_low_coverage",
            ]
        ].to_dict(orient="records"),
    })
    return out, qa


def _validate_monthly_output(df: pd.DataFrame, config_code: str) -> None:
    if list(df.columns) != MONTHLY_OUTPUT_COLUMNS:
        raise RuntimeError(
            f"{config_code}: monthly output schema is incorrect."
        )
    if df["Value"].isna().any():
        raise RuntimeError(
            f"{config_code}: monthly output contains NaN Value."
        )


def build_monthly_averages(
    input_prefix: str = "zentra_final_12_configs",
    output_prefix: str = "Zentra_monthly_averages",
    timezone_name: str = "America/Los_Angeles",
    average_method: str = "daily_mean",
    exclude_error_rows: Any = True,
    conflict_policy: str = "keep_distinct",
    minimum_day_coverage_ratio: float = 0.8,
):
    """
    Safe full monthly rebuild.

    The final 12-config CSVs are intentionally source-preserving. Before any
    averaging, this function removes only exact duplicate observations where
    all available event-identifying fields AND measurement, Value, and Units
    match. Rows with a different Value, Units, or measurement are retained.

    All 12 outputs are computed and validated locally first. Existing production
    monthly files are NOT overwritten with empty/error outputs on failure.
    """
    exclude_error_rows = str(exclude_error_rows).strip().lower() in {
        "true", "1", "yes", "y"
    } if not isinstance(exclude_error_rows, bool) else exclude_error_rows

    publish: dict[str, pd.DataFrame] = {}
    summaries: dict[str, dict] = {}

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"
        if not _exists(input_prefix, filename):
            raise RuntimeError(
                f"Missing required final config CSV: "
                f"{input_prefix}/{filename}"
            )

        df = _download_csv(
            input_prefix,
            filename,
            f"_monthly_input_{filename}",
        )
        monthly, qa = _build_monthly(
            df,
            timezone_name=timezone_name,
            average_method=average_method,
            exclude_error_rows=exclude_error_rows,
            conflict_policy=conflict_policy,
            minimum_day_coverage_ratio=float(
                minimum_day_coverage_ratio
            ),
        )
        _validate_monthly_output(monthly, config_code)
        publish[config_code] = monthly
        summaries[config_code] = {
            "configuration_code": config_code,
            "status": "ok",
            "input_rows": int(len(df)),
            **qa,
        }

    # Publish only after all 12 passed validation.
    for config_code, monthly in publish.items():
        folder = f"{output_prefix}/{config_code}"
        filename = f"{config_code}_monthly_averages.csv"
        summary_file = f"{config_code}_monthly_summary.json"

        monthly.to_csv(filename, index=False)
        faasr_put_file(
            local_file=filename,
            remote_folder=folder,
            remote_file=filename,
        )
        _put_json(
            summaries[config_code],
            folder,
            summary_file,
        )

    build_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "mode": "full",
        "input_prefix": input_prefix,
        "output_prefix": output_prefix,
        "timezone_name": timezone_name,
        "average_method": average_method,
        "exclude_error_rows": exclude_error_rows,
        "conflict_policy": conflict_policy,
        "duplicate_policy": "exact_observation_value_units",
        "duplicate_rule": (
            "Remove only rows matching all available event identity fields "
            "plus measurement, Value and Units; ignore source_file."
        ),
        "minimum_day_coverage_ratio": float(
            minimum_day_coverage_ratio
        ),
        "configs": summaries,
    }
    _put_json(
        build_summary,
        output_prefix,
        "_monthly_build_summary.json",
    )
    faasr_log("Safe full monthly averages build complete.")


def update_monthly_averages_incremental(
    input_prefix: str = "zentra_final_12_configs",
    output_prefix: str = "Zentra_monthly_averages",
    processing_state_prefix: str = "zentra_processing_state",
    daily_12_summary_file: str = "daily_12config_update_summary_latest.json",
    timezone_name: str = "America/Los_Angeles",
    average_method: str = "daily_mean",
    exclude_error_rows: Any = True,
    conflict_policy: str = "keep_distinct",
    minimum_day_coverage_ratio: float = 0.8,
):
    """
    Daily monthly updater.

    Uses affected_configs / affected_months_by_config from the exact daily
    12-config update summary. It does not rebuild all 12 histories each day.
    For each affected config it recomputes only the affected month rows from the
    authoritative final config CSV, then merges those rows into the existing
    monthly output.
    """
    if not _exists(processing_state_prefix, daily_12_summary_file):
        raise RuntimeError(
            f"Missing daily 12-config summary: "
            f"{processing_state_prefix}/{daily_12_summary_file}"
        )

    update_info = _download_json(
        processing_state_prefix,
        daily_12_summary_file,
        "_daily_12_summary.json",
    )

    affected = update_info.get("affected_months_by_config", {})
    if not isinstance(affected, dict):
        affected = {}

    run_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "incremental",
        "daily_12_summary_status": update_info.get("status"),
        "affected_months_by_config": affected,
        "duplicate_policy": "exact_observation_value_units",
        "configs": {},
    }

    if not affected:
        run_summary["status"] = "no_affected_months"
        _put_json(
            run_summary,
            output_prefix,
            "_monthly_incremental_summary_latest.json",
        )
        faasr_log("Incremental monthly update: nothing affected.")
        return

    exclude_error_rows = str(exclude_error_rows).strip().lower() in {
        "true", "1", "yes", "y"
    } if not isinstance(exclude_error_rows, bool) else exclude_error_rows

    publish = {}

    for config_code, month_keys in affected.items():
        if config_code not in CONFIG_CODES:
            continue
        month_keys = sorted(set(str(x) for x in month_keys))
        if not month_keys:
            continue

        input_file = f"{config_code}.csv"
        if not _exists(input_prefix, input_file):
            raise RuntimeError(
                f"Missing final config for incremental monthly update: "
                f"{input_prefix}/{input_file}"
            )

        df = _download_csv(
            input_prefix,
            input_file,
            f"_monthly_inc_input_{input_file}",
        )

        recomputed_all, qa = _build_monthly(
            df,
            timezone_name=timezone_name,
            average_method=average_method,
            exclude_error_rows=exclude_error_rows,
            conflict_policy=conflict_policy,
            minimum_day_coverage_ratio=float(
                minimum_day_coverage_ratio
            ),
        )

        # date_time is a local calendar date; derive YYYY-MM for replacement.
        rec_month = pd.to_datetime(
            recomputed_all["date_time"],
            errors="coerce",
        ).dt.strftime("%Y-%m")
        recomputed = recomputed_all[
            rec_month.isin(month_keys)
        ].copy()

        folder = f"{output_prefix}/{config_code}"
        output_file = f"{config_code}_monthly_averages.csv"

        if _exists(folder, output_file):
            existing = _download_csv(
                folder,
                output_file,
                f"_monthly_existing_{output_file}",
            )
            missing_cols = [
                c for c in MONTHLY_OUTPUT_COLUMNS
                if c not in existing.columns
            ]
            if missing_cols:
                raise RuntimeError(
                    f"Existing monthly file for {config_code} has "
                    f"wrong schema. Missing: {missing_cols}"
                )
            existing_month = pd.to_datetime(
                existing["date_time"],
                errors="coerce",
            ).dt.strftime("%Y-%m")
            existing = existing[
                ~existing_month.isin(month_keys)
            ].copy()
            merged = pd.concat(
                [existing[MONTHLY_OUTPUT_COLUMNS], recomputed],
                ignore_index=True,
            )
        else:
            # If the monthly output does not exist yet, initialize it fully.
            merged = recomputed_all.copy()

        merged = merged.sort_values(
            [
                "logger_serial_number",
                "port_num",
                "measurement",
                "date_time",
                "Units",
            ]
        ).reset_index(drop=True)

        _validate_monthly_output(merged, config_code)
        publish[config_code] = merged
        run_summary["configs"][config_code] = {
            "status": "ok",
            "affected_months": month_keys,
            "replacement_rows": int(len(recomputed)),
            **qa,
        }

    # Prepare all first, then publish.
    for config_code, merged in publish.items():
        folder = f"{output_prefix}/{config_code}"
        output_file = f"{config_code}_monthly_averages.csv"
        merged.to_csv(output_file, index=False)
        faasr_put_file(
            local_file=output_file,
            remote_folder=folder,
            remote_file=output_file,
        )
        _put_json(
            run_summary["configs"][config_code],
            folder,
            f"{config_code}_monthly_summary.json",
        )

    run_summary["status"] = "success"
    _put_json(
        run_summary,
        output_prefix,
        "_monthly_incremental_summary_latest.json",
    )
    faasr_log("Incremental monthly averages update complete.")


def finish_monthly_averages():
    faasr_log("Monthly averages workflow finished successfully.")
