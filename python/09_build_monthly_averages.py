
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

# Exact output CSV format requested.
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

# User-facing grouping fields.
# measurement is included so different sensor measurements are not averaged together.
# Units is added internally so values with different units are not mixed.
GROUP_COLUMNS = [
    "Location_code",
    "Shade_zone_code",
    "Irrigation_code",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "measurement",
    "Units",
    "_month_key",
]


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _s3_object_exists(remote_folder: str, remote_file: str) -> bool:
    """
    Safely check whether an object exists before calling faasr_get_file.

    FaaSr's faasr_get_file can terminate the action if the object is missing,
    so missing files must be checked with faasr_get_folder_list first.
    """
    key = f"{remote_folder.rstrip('/')}/{remote_file}" if remote_folder else remote_file

    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=key))
    except Exception as exc:
        faasr_log(f"Could not check S3 object existence for {key}: {exc}")
        return False

    for obj in objects:
        obj = str(obj).strip()
        if obj == key or obj.endswith(f"/{remote_file}") or obj == remote_file:
            return True

    return False


def _download_csv(remote_folder: str, remote_file: str, local_name: str) -> pd.DataFrame:
    faasr_get_file(local_file=local_name, remote_folder=remote_folder, remote_file=remote_file)
    return pd.read_csv(local_name)


def _put_json(obj: dict, remote_folder: str, remote_file: str) -> None:
    with open(remote_file, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    faasr_put_file(local_file=remote_file, remote_folder=remote_folder, remote_file=remote_file)


def _standardize_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert the final 12-config CSV column names into the exact requested
    monthly output column names.

    The final 12-config CSVs use snake_case columns such as location_code,
    shade_zone_code, irrigation_code, datetime, value, and units.
    """
    df = df.copy()

    rename_map = {
        "location_code": "Location_code",
        "shade_zone_code": "Shade_zone_code",
        "irrigation_code": "Irrigation_code",
        "units": "Units",
        "value": "Value",
        "Measurement": "measurement",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    required_input_cols = [
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

    for col in required_input_cols:
        if col not in df.columns:
            df[col] = pd.NA

    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")

    if "port_num" in df.columns:
        df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").astype("Int64")

    # Important:
    # Use the date/time exactly as written in the final CSV to decide monthly
    # cutoffs. Do not convert to UTC/local time again here.
    #
    # This handles strings like:
    #   2024-04-04 00:00:00+00:00
    #   2024-04-04T00:00:00Z
    #   2024-04-04
    date_text = df["datetime"].astype(str).str.strip().str.slice(0, 10)
    df["_csv_date"] = pd.to_datetime(date_text, errors="coerce")
    df = df.dropna(subset=["_csv_date"])

    # Calendar month grouping derived from the exact CSV date string.
    df["_month_key"] = df["_csv_date"].dt.to_period("M").astype(str)

    return df


def _build_monthly_for_config(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build monthly averages in the exact requested CSV format.

    Requested grouping:
      Location_code, Shade_zone_code, Irrigation_code, logger_name,
      logger_serial_number, port_num, measurement

    We also include port_description because it is in the requested output,
    measurement because different sensor measurements should not be averaged
    together, and Units because values with different units should never be
    averaged together.
    """
    df = _standardize_input_columns(df)

    if df.empty:
        return pd.DataFrame(columns=MONTHLY_OUTPUT_COLUMNS)

    monthly = (
        df.groupby(GROUP_COLUMNS, dropna=False)
        .agg(
            Value=("Value", "mean"),
            _monthly_cutoff_date=("_csv_date", "max"),
        )
        .reset_index()
    )

    # date_time is the monthly cutoff date from the actual CSV data, formatted YYYY-MM-DD.
    # For a complete past month, this is usually the last date available in that month.
    # For the current incomplete month, this is the latest date currently present.
    monthly["date_time"] = monthly["_monthly_cutoff_date"].dt.strftime("%Y-%m-%d")

    monthly = monthly[MONTHLY_OUTPUT_COLUMNS]

    sort_cols = [
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
    monthly = monthly.sort_values(sort_cols).reset_index(drop=True)

    return monthly


def build_monthly_averages(
    input_prefix: str = "zentra_final_12_configs",
    output_prefix: str = "Zentra_monthly_averages",
):
    """
    Rebuild monthly averages from the 12 final configuration CSVs.

    Input:
      zentra_final_12_configs/<CONFIG>.csv

    Output:
      Zentra_monthly_averages/<CONFIG>/<CONFIG>_monthly_averages.csv
      Zentra_monthly_averages/<CONFIG>/<CONFIG>_monthly_summary.json
      Zentra_monthly_averages/_monthly_build_summary.json

    The output CSV has exactly these columns:
      Location_code, Shade_zone_code, Irrigation_code, logger_name,
      logger_serial_number, port_num, port_description, measurement, date_time, Value, Units

    Existing monthly average files are overwritten with the same filenames.
    A separate delete step is not needed.
    """
    input_prefix = input_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")

    build_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_prefix": input_prefix,
        "output_prefix": output_prefix,
        "mode": "full_rebuild_monthly_averages_exact_requested_format",
        "output_columns": MONTHLY_OUTPUT_COLUMNS,
        "grouping_note": (
            "Grouped by requested fields plus port_description, measurement, and Units. "
            "measurement is included to avoid averaging different sensor measurements together; "
            "Units is included to avoid averaging values with different units together."
        ),
        "date_time_note": (
            "date_time is the monthly cutoff date from the actual CSV datetime values, "
            "formatted as YYYY-MM-DD."
        ),
        "configs": {},
    }

    for config_code in CONFIG_CODES:
        input_file = f"{config_code}.csv"
        output_folder = f"{output_prefix}/{config_code}"
        output_file = f"{config_code}_monthly_averages.csv"
        summary_file = f"{config_code}_monthly_summary.json"

        if not _s3_object_exists(input_prefix, input_file):
            faasr_log(f"Missing final config CSV: {input_prefix}/{input_file}")

            monthly_df = pd.DataFrame(columns=MONTHLY_OUTPUT_COLUMNS)
            monthly_df.to_csv(output_file, index=False)
            faasr_put_file(local_file=output_file, remote_folder=output_folder, remote_file=output_file)

            config_summary = {
                "configuration_code": config_code,
                "status": "missing_input_final_csv",
                "input_path": f"{input_prefix}/{input_file}",
                "output_path": f"{output_folder}/{output_file}",
                "monthly_rows": 0,
            }
            _put_json(config_summary, output_folder, summary_file)
            build_summary["configs"][config_code] = config_summary
            continue

        try:
            df = _download_csv(input_prefix, input_file, f"final_{input_file}")
            monthly_df = _build_monthly_for_config(df)

            monthly_df.to_csv(output_file, index=False)
            faasr_put_file(local_file=output_file, remote_folder=output_folder, remote_file=output_file)

            config_summary = {
                "configuration_code": config_code,
                "status": "ok",
                "input_path": f"{input_prefix}/{input_file}",
                "output_path": f"{output_folder}/{output_file}",
                "input_rows": int(len(df)),
                "monthly_rows": int(len(monthly_df)),
                "first_date_time": None if monthly_df.empty else str(monthly_df["date_time"].min()),
                "last_date_time": None if monthly_df.empty else str(monthly_df["date_time"].max()),
                "logger_serial_numbers": (
                    []
                    if monthly_df.empty
                    else sorted(monthly_df["logger_serial_number"].dropna().astype(str).unique().tolist())
                ),
                "ports": (
                    []
                    if monthly_df.empty
                    else sorted(monthly_df["port_num"].dropna().astype(str).unique().tolist())
                ),
                "measurements": (
                    []
                    if monthly_df.empty
                    else sorted(monthly_df["measurement"].dropna().astype(str).unique().tolist())
                ),
                "units": (
                    []
                    if monthly_df.empty
                    else sorted(monthly_df["Units"].dropna().astype(str).unique().tolist())
                ),
            }

            _put_json(config_summary, output_folder, summary_file)
            build_summary["configs"][config_code] = config_summary

            faasr_log(
                f"Built monthly averages for {config_code}: "
                f"input_rows={len(df)}, monthly_rows={len(monthly_df)}"
            )

        except Exception as exc:
            faasr_log(f"Failed to build monthly averages for {config_code}: {exc}")

            monthly_df = pd.DataFrame(columns=MONTHLY_OUTPUT_COLUMNS)
            monthly_df.to_csv(output_file, index=False)
            faasr_put_file(local_file=output_file, remote_folder=output_folder, remote_file=output_file)

            config_summary = {
                "configuration_code": config_code,
                "status": "failed",
                "input_path": f"{input_prefix}/{input_file}",
                "output_path": f"{output_folder}/{output_file}",
                "error": str(exc),
                "monthly_rows": 0,
            }
            _put_json(config_summary, output_folder, summary_file)
            build_summary["configs"][config_code] = config_summary

    _put_json(build_summary, output_prefix, "_monthly_build_summary.json")
    faasr_log("Monthly averages workflow complete.")


def finish_monthly_averages():
    """
    Terminal no-op node for FaaSr DAG validation.
    """
    faasr_log("Monthly averages workflow finished successfully.")
