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

# Orchardgrass trial mapping.
# Final segregation rule:
# 12 CSVs = Location + Shade Zone + Irrigation.
# Rows inside each CSV are selected by logger_serial_number + port_num.
MAPPING_ROWS = [
    # config, loc_code, loc, shade_code, shade, irr_code, irr, logger, serial, port, description, notes
    ("C_O_N", "C", "Control", "O", "Open", "N", "None/no irrigation", "NEWAg #7", "z6-19600", 2, "5-10 cm soil", ""),
    ("C_O_N", "C", "Control", "O", "Open", "N", "None/no irrigation", "NEWAg #7", "z6-19600", 4, "20-30 cm soil", ""),
    ("C_O_N", "C", "Control", "O", "Open", "N", "None/no irrigation", "NEWAg #7", "z6-19600", 5, "IR camera", ""),

    ("C_O_D", "C", "Control", "O", "Open", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #13", "z6-12196", 3, "20-30 cm soil", ""),
    ("C_O_D", "C", "Control", "O", "Open", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #13", "z6-12196", 5, "IR camera", ""),
    ("C_O_D", "C", "Control", "O", "Open", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #13", "z6-12196", 6, "5-10 cm soil", "Known issue: NEWAg #9 port 2 moved to NEWAg #13 port 6 at some point."),
    ("C_O_D", "C", "Control", "O", "Open", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #9", "z6-19602", 2, "5-10 cm soil", "Known issue: NEWAg #9 port 2 moved to NEWAg #13 port 6 at some point."),

    ("C_O_F", "C", "Control", "O", "Open", "F", "Full (100% of daily evapotranspiration)", "NEWAg #11", "z6-19604", 3, "20-30 cm soil", ""),
    ("C_O_F", "C", "Control", "O", "Open", "F", "Full (100% of daily evapotranspiration)", "NEWAg #4", "z6-19597", 2, "5-10 cm soil", ""),
    ("C_O_F", "C", "Control", "O", "Open", "F", "Full (100% of daily evapotranspiration)", "NEWAg #4", "z6-19597", 3, "IR camera", ""),

    ("S_W_N", "S", "Solar Array", "W", "West", "N", "None/no irrigation", "NEWAg #1", "z6-19594", 3, "20-30 cm soil", ""),
    ("S_W_N", "S", "Solar Array", "W", "West", "N", "None/no irrigation", "NEWAg #1", "z6-19594", 5, "5-10 cm soil", ""),
    ("S_W_N", "S", "Solar Array", "W", "West", "N", "None/no irrigation", "NEWAg #1", "z6-19594", 6, "IR camera", ""),

    ("S_C_N", "S", "Solar Array", "C", "Center", "N", "None/no irrigation", "NEWAg #1", "z6-19594", 2, "20-30 cm soil", ""),
    ("S_C_N", "S", "Solar Array", "C", "Center", "N", "None/no irrigation", "NEWAg #1", "z6-19594", 4, "5-10 cm soil", ""),
    ("S_C_N", "S", "Solar Array", "C", "Center", "N", "None/no irrigation", "NEWAg #6", "z6-19599", 3, "IR camera", ""),

    ("S_E_N", "S", "Solar Array", "E", "East", "N", "None/no irrigation", "NEWAg #15", "z6-12197", 2, "5-10 cm soil", ""),
    ("S_E_N", "S", "Solar Array", "E", "East", "N", "None/no irrigation", "NEWAg #15", "z6-12197", 3, "20-30 cm soil", ""),
    ("S_E_N", "S", "Solar Array", "E", "East", "N", "None/no irrigation", "NEWAg #15", "z6-12197", 4, "IR camera", ""),

    ("S_W_D", "S", "Solar Array", "W", "West", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #2", "z6-19595", 3, "20-30 cm soil", ""),
    ("S_W_D", "S", "Solar Array", "W", "West", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #2", "z6-19595", 5, "5-10 cm soil", ""),
    ("S_W_D", "S", "Solar Array", "W", "West", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #2", "z6-19595", 6, "IR camera", ""),

    ("S_C_D", "S", "Solar Array", "C", "Center", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #2", "z6-19595", 2, "20-30 cm soil", ""),
    ("S_C_D", "S", "Solar Array", "C", "Center", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #2", "z6-19595", 4, "5-10 cm soil", ""),
    ("S_C_D", "S", "Solar Array", "C", "Center", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #5", "z6-19598", 3, "IR camera", "Known issue: NEWAg #5 port 3 moved to NEWAg #5 port 4, unsure of date."),
    ("S_C_D", "S", "Solar Array", "C", "Center", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #5", "z6-19598", 4, "IR camera", "Known issue: NEWAg #5 port 3 moved to NEWAg #5 port 4, unsure of date."),

    ("S_E_D", "S", "Solar Array", "E", "East", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #14", "z6-12202", 2, "5-10 cm soil", ""),
    ("S_E_D", "S", "Solar Array", "E", "East", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #14", "z6-12202", 3, "20-30 cm soil", ""),
    ("S_E_D", "S", "Solar Array", "E", "East", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #14", "z6-12202", 4, "IR camera", ""),

    ("S_W_F", "S", "Solar Array", "W", "West", "F", "Full (100% of daily evapotranspiration)", "NEWAg #3", "z6-19596", 3, "20-30 cm soil", ""),
    ("S_W_F", "S", "Solar Array", "W", "West", "F", "Full (100% of daily evapotranspiration)", "NEWAg #3", "z6-19596", 5, "5-10 cm soil", ""),
    ("S_W_F", "S", "Solar Array", "W", "West", "F", "Full (100% of daily evapotranspiration)", "NEWAg #3", "z6-19596", 6, "IR camera", ""),

    ("S_C_F", "S", "Solar Array", "C", "Center", "F", "Full (100% of daily evapotranspiration)", "NEWAg #3", "z6-19596", 2, "20-30 cm soil", ""),
    ("S_C_F", "S", "Solar Array", "C", "Center", "F", "Full (100% of daily evapotranspiration)", "NEWAg #3", "z6-19596", 4, "5-10 cm soil", ""),
    ("S_C_F", "S", "Solar Array", "C", "Center", "F", "Full (100% of daily evapotranspiration)", "NEWAg #10", "z6-19603", 4, "IR camera", "Known issue: NEWAg #10 may have logger box issues; data good up to 2026-05-01 except port 4 intermittent over winter."),

    ("S_E_F", "S", "Solar Array", "E", "East", "F", "Full (100% of daily evapotranspiration)", "NEWAg #10", "z6-19603", 2, "5-10 cm soil", "Known issue: NEWAg #10 may have logger box issues; data good up to 2026-05-01 except port 4 intermittent over winter."),
    ("S_E_F", "S", "Solar Array", "E", "East", "F", "Full (100% of daily evapotranspiration)", "NEWAg #10", "z6-19603", 3, "20-30 cm soil", "Known issue: NEWAg #10 may have logger box issues; data good up to 2026-05-01 except port 4 intermittent over winter."),
    ("S_E_F", "S", "Solar Array", "E", "East", "F", "Full (100% of daily evapotranspiration)", "NEWAg #10", "z6-19603", 5, "IR camera", "Known issue: NEWAg #10 may have logger box issues; data good up to 2026-05-01 except port 4 intermittent over winter."),
]

MAPPING_COLUMNS = [
    "configuration_code",
    "location_code",
    "location",
    "shade_zone_code",
    "shade_zone",
    "irrigation_code",
    "irrigation",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "notes",
]

OUTPUT_COLUMNS = [
    "configuration_code",
    "location_code",
    "location",
    "shade_zone_code",
    "shade_zone",
    "irrigation_code",
    "irrigation",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "notes",
    "timestamp_utc",
    "tz_offset",
    "datetime",
    "mrid",
    "measurement",
    "value",
    "units",
    "precision",
    "sub_sensor_index",
    "sensor_sn",
    "sensor_name",
    "error_flag",
    "error_description",
    "sensor_meta_errors",
    "source_file",
]

CONFIG_CODES = [
    "C_O_N", "C_O_D", "C_O_F",
    "S_W_N", "S_C_N", "S_E_N",
    "S_W_D", "S_C_D", "S_E_D",
    "S_W_F", "S_C_F", "S_E_F",
]


def _mapping_df() -> pd.DataFrame:
    return pd.DataFrame(MAPPING_ROWS, columns=MAPPING_COLUMNS)


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _remote_folder_and_file(default_folder: str, obj: str) -> tuple[str, str]:
    obj = str(obj).strip()
    if "/" in obj:
        return "/".join(obj.split("/")[:-1]), obj.split("/")[-1]
    return default_folder, obj


def _list_csv_files(folder: str, max_files: str = "ALL") -> list[tuple[str, str, str]]:
    faasr_log(f"Listing CSV files under {folder}")
    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=folder))
    except Exception as exc:
        faasr_log(f"No files found or could not list folder {folder}: {exc}")
        return []

    files = []
    for obj in objects:
        obj = str(obj).strip()
        if not obj.lower().endswith(".csv"):
            continue
        remote_folder, remote_file = _remote_folder_and_file(folder, obj)
        files.append((remote_folder, remote_file, f"{remote_folder}/{remote_file}"))
    files = sorted(set(files), key=lambda x: x[2])
    if str(max_files).strip().upper() != "ALL":
        files = files[: int(max_files)]
    faasr_log(f"Found {len(files)} CSV files under {folder}")
    return files


def _download_csv(remote_folder: str, remote_file: str, local_name: str) -> pd.DataFrame:
    faasr_get_file(local_file=local_name, remote_folder=remote_folder, remote_file=remote_file)
    return pd.read_csv(local_name)


def _write_json(obj: dict, local_file: str) -> None:
    with open(local_file, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def _put_json(obj: dict, remote_folder: str, remote_file: str) -> None:
    _write_json(obj, remote_file)
    faasr_put_file(local_file=remote_file, remote_folder=remote_folder, remote_file=remote_file)


def _standardize_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize common column name variants without changing the long-format data.

    Important: if a port has multiple measurements/values at the same timestamp,
    this script keeps them as separate rows. It does not pivot or collapse them.
    """
    df = df.copy()

    if "port_num" not in df.columns:
        for candidate in ["port_number", "port"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "port_num"})
                break

    if "datetime" not in df.columns:
        for candidate in ["timestamp", "Timestamp", "date_time", "time", "DateTime"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "datetime"})
                break

    return df


def _measurement_counts(df: pd.DataFrame) -> list[dict[str, Any]]:
    """
    Small QA summary to confirm that multiple measurement/value rows per port are preserved.
    """
    if df.empty:
        return []
    group_cols = [c for c in ["logger_serial_number", "port_num", "measurement", "units"] if c in df.columns]
    if not group_cols:
        return [{"rows": int(len(df))}]
    return (
        df.groupby(group_cols, dropna=False)
        .size()
        .reset_index(name="rows")
        .to_dict(orient="records")
    )


def _prepare_raw_df(df: pd.DataFrame, serial: str, source_path: str) -> pd.DataFrame:
    df = _standardize_raw_columns(df)
    df["logger_serial_number"] = serial
    df["source_file"] = source_path
    if "port_num" in df.columns:
        df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").astype("Int64")
    if "timestamp_utc" in df.columns:
        df["timestamp_utc"] = pd.to_numeric(df["timestamp_utc"], errors="coerce")
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=True)
    return df


def _dedupe_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    dedupe_cols = [
        c for c in [
            "logger_serial_number",
            "port_num",
            "timestamp_utc",
            "datetime",
            "measurement",
            "value",
            "units",
            "sub_sensor_index",
            "sensor_sn",
        ] if c in df.columns
    ]
    if dedupe_cols:
        df = df.drop_duplicates(subset=dedupe_cols, keep="first")
    else:
        df = df.drop_duplicates()
    sort_cols = [
        c for c in [
            "configuration_code",
            "timestamp_utc",
            "datetime",
            "logger_serial_number",
            "port_num",
            "measurement",
        ] if c in df.columns
    ]
    if sort_cols:
        df = df.sort_values(sort_cols)
    return df.reset_index(drop=True)



def _load_existing_source_files(output_prefix: str) -> dict[str, set[str]]:
    """
    Read existing final 12 CSVs and collect the source_file values already included.

    Returns:
      {
        "C_O_N": {"zentra_raw_backfill/z6-19600/file1.csv", ...},
        ...
      }

    This enables incremental Phase 2 runs:
    - If a raw source file is already present in the corresponding final CSV,
      it does not need to be staged again.
    - If a final CSV does not exist yet, its processed-source set is empty.
    """
    existing_sources: dict[str, set[str]] = {code: set() for code in CONFIG_CODES}

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"
        try:
            df_existing = _download_csv(output_prefix, filename, f"_existing_sources_{filename}")
            if "source_file" in df_existing.columns:
                existing_sources[config_code] = set(
                    df_existing["source_file"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .tolist()
                )
                faasr_log(
                    f"Existing {filename}: found "
                    f"{len(existing_sources[config_code])} processed source files"
                )
            else:
                faasr_log(f"Existing {filename} has no source_file column; treating as not processed.")
        except Exception as exc:
            faasr_log(f"No existing final file for {config_code} or could not read it: {exc}")

    return existing_sources


def _raw_source_needed_for_serial(
    mapping: pd.DataFrame,
    serial: str,
    source_path: str,
    existing_sources_by_config: dict[str, set[str]],
) -> bool:
    """
    A raw source file is needed if at least one final configuration that uses
    this serial does not already contain that source_file.

    This is safer than checking globally, because one logger can contribute
    to multiple final configuration CSVs through different ports.
    """
    config_codes = (
        mapping[mapping["logger_serial_number"].astype(str) == str(serial)]
        ["configuration_code"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    if not config_codes:
        return True

    for config_code in config_codes:
        if source_path not in existing_sources_by_config.get(config_code, set()):
            return True

    return False


def _merge_existing_and_generated(existing_df: pd.DataFrame | None, generated_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge a generated incremental config CSV with the existing final config CSV.
    Keep long-format rows and remove true duplicates only.
    """
    pieces = []

    if existing_df is not None and not existing_df.empty:
        pieces.append(existing_df)

    if generated_df is not None and not generated_df.empty:
        pieces.append(generated_df)

    if pieces:
        merged = pd.concat(pieces, ignore_index=True)
        merged = _standardize_raw_columns(merged)
        if "port_num" in merged.columns:
            merged["port_num"] = pd.to_numeric(merged["port_num"], errors="coerce").astype("Int64")
        if "timestamp_utc" in merged.columns:
            merged["timestamp_utc"] = pd.to_numeric(merged["timestamp_utc"], errors="coerce")
        if "datetime" in merged.columns:
            merged["datetime"] = pd.to_datetime(merged["datetime"], errors="coerce", utc=True)
        merged = _dedupe_and_sort(merged)
    else:
        merged = pd.DataFrame(columns=OUTPUT_COLUMNS)

    for col in OUTPUT_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    extra_cols = [c for c in merged.columns if c not in OUTPUT_COLUMNS]
    merged = merged[OUTPUT_COLUMNS + extra_cols]

    return merged


def read_zentra_raw_files(
    raw_prefix: str = "zentra_raw_backfill",
    staging_prefix: str = "zentra_phase2_staging",
    max_files_per_serial: str = "ALL",
    output_prefix: str = "zentra_final_12_configs",
):
    """
    Function 1:
    Incrementally read raw historical backfill CSV files from S3 and stage only
    raw files that are not already represented in the existing final 12 CSVs.

    Input:
      zentra_raw_backfill/<serial>/*.csv

    Output:
      zentra_phase2_staging/raw_by_serial/<serial>_raw_combined.csv
      zentra_phase2_staging/raw_manifest.json
      zentra_phase2_staging/mapping_used.csv
    """
    mapping = _mapping_df()
    serials = sorted(mapping["logger_serial_number"].unique().tolist())

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_prefix": raw_prefix,
        "staging_prefix": staging_prefix,
        "output_prefix": output_prefix,
        "max_files_per_serial": max_files_per_serial,
        "mode": "incremental_by_source_file",
        "serials": {},
    }

    # Save mapping for transparency and for the next step.
    mapping_file = "mapping_used.csv"
    mapping.to_csv(mapping_file, index=False)
    faasr_put_file(local_file=mapping_file, remote_folder=staging_prefix, remote_file=mapping_file)

    existing_sources_by_config = _load_existing_source_files(output_prefix)

    for serial in serials:
        raw_folder = f"{raw_prefix}/{serial}"
        raw_files = _list_csv_files(raw_folder, max_files=max_files_per_serial)

        needed_raw_files = []
        skipped_already_processed = []

        for remote_folder, remote_file, source_path in raw_files:
            if _raw_source_needed_for_serial(
                mapping=mapping,
                serial=serial,
                source_path=source_path,
                existing_sources_by_config=existing_sources_by_config,
            ):
                needed_raw_files.append((remote_folder, remote_file, source_path))
            else:
                skipped_already_processed.append(source_path)

        faasr_log(
            f"{serial}: raw_files={len(raw_files)}, "
            f"new_or_needed={len(needed_raw_files)}, "
            f"skipped_already_processed={len(skipped_already_processed)}"
        )

        pieces = []
        for i, (remote_folder, remote_file, source_path) in enumerate(needed_raw_files):
            try:
                df = _download_csv(remote_folder, remote_file, f"raw_{serial}_{i}.csv")
                pieces.append(_prepare_raw_df(df, serial=serial, source_path=source_path))
            except Exception as exc:
                faasr_log(f"Skipping unreadable raw file {source_path}: {exc}")

        if pieces:
            combined = pd.concat(pieces, ignore_index=True)
            combined = _dedupe_and_sort(combined)
        else:
            combined = pd.DataFrame()

        staged_file = f"{serial}_raw_combined.csv"
        combined.to_csv(staged_file, index=False)
        faasr_put_file(
            local_file=staged_file,
            remote_folder=f"{staging_prefix}/raw_by_serial",
            remote_file=staged_file,
        )

        manifest["serials"][serial] = {
            "raw_files_found": len(raw_files),
            "raw_files_staged_new_or_needed": len(needed_raw_files),
            "raw_files_skipped_already_processed": len(skipped_already_processed),
            "staged_rows": int(len(combined)),
            "staged_path": f"{staging_prefix}/raw_by_serial/{staged_file}",
            "measurement_counts": _measurement_counts(combined),
        }
        faasr_log(f"Staged {len(combined)} rows for {serial}")

    _put_json(manifest, staging_prefix, "raw_manifest.json")
    faasr_log("Function 1 complete: raw files read and staged.")


def form_12_config_csvs(
    staging_prefix: str = "zentra_phase2_staging",
    generated_prefix: str = "zentra_phase2_staging/generated_12_configs",
):
    """
    Function 2:
    Form the 12 configuration CSVs from staged per-logger raw files.

    Input:
      zentra_phase2_staging/raw_by_serial/<serial>_raw_combined.csv
      zentra_phase2_staging/mapping_used.csv

    Output:
      zentra_phase2_staging/generated_12_configs/<CONFIG>.csv
      zentra_phase2_staging/generated_12_configs/build_summary.json
    """
    mapping = _download_csv(staging_prefix, "mapping_used.csv", "mapping_used.csv")
    if "port_num" in mapping.columns:
        mapping["port_num"] = pd.to_numeric(mapping["port_num"], errors="coerce").astype("Int64")

    raw_by_serial = {}
    for serial in sorted(mapping["logger_serial_number"].unique().tolist()):
        staged_file = f"{serial}_raw_combined.csv"
        try:
            df_serial = _download_csv(
                f"{staging_prefix}/raw_by_serial",
                staged_file,
                f"staged_{serial}.csv",
            )
            df_serial = _standardize_raw_columns(df_serial)
            if "port_num" in df_serial.columns:
                df_serial["port_num"] = pd.to_numeric(df_serial["port_num"], errors="coerce").astype("Int64")
            if "datetime" in df_serial.columns:
                df_serial["datetime"] = pd.to_datetime(df_serial["datetime"], errors="coerce", utc=True)
            raw_by_serial[serial] = df_serial
            faasr_log(f"Loaded staged raw for {serial}: {len(df_serial)} rows")
        except Exception as exc:
            faasr_log(f"No staged raw file for {serial}; using empty dataframe. Error: {exc}")
            raw_by_serial[serial] = pd.DataFrame()

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_prefix": generated_prefix,
        "configs": {},
    }

    for config_code in CONFIG_CODES:
        config_map = mapping[mapping["configuration_code"] == config_code]
        pieces = []

        for _, m in config_map.iterrows():
            serial = str(m["logger_serial_number"])
            port = int(m["port_num"])
            df_serial = raw_by_serial.get(serial, pd.DataFrame())

            if df_serial.empty or "port_num" not in df_serial.columns:
                continue

            selected = df_serial[df_serial["port_num"] == port].copy()
            if selected.empty:
                continue

            for col in [
                "configuration_code",
                "location_code",
                "location",
                "shade_zone_code",
                "shade_zone",
                "irrigation_code",
                "irrigation",
                "logger_name",
                "port_description",
                "notes",
            ]:
                selected[col] = m[col]

            selected["logger_serial_number"] = serial
            selected["port_num"] = port
            pieces.append(selected)

        if pieces:
            config_df = pd.concat(pieces, ignore_index=True)
            config_df = _dedupe_and_sort(config_df)
        else:
            config_df = pd.DataFrame(columns=OUTPUT_COLUMNS)

        for col in OUTPUT_COLUMNS:
            if col not in config_df.columns:
                config_df[col] = pd.NA

        extra_cols = [c for c in config_df.columns if c not in OUTPUT_COLUMNS]
        config_df = config_df[OUTPUT_COLUMNS + extra_cols]

        out_file = f"{config_code}.csv"
        config_df.to_csv(out_file, index=False)
        faasr_put_file(local_file=out_file, remote_folder=generated_prefix, remote_file=out_file)

        summary["configs"][config_code] = {
            "rows": int(len(config_df)),
            "generated_path": f"{generated_prefix}/{out_file}",
            "ports_used": config_map[["logger_name", "logger_serial_number", "port_num", "port_description"]].to_dict(orient="records"),
            "measurement_counts": _measurement_counts(config_df),
        }
        faasr_log(f"Generated {out_file}: {len(config_df)} rows")

    _put_json(summary, generated_prefix, "build_summary.json")
    faasr_log("Function 2 complete: 12 configuration CSVs formed.")


def upload_12_config_csvs(
    generated_prefix: str = "zentra_phase2_staging/generated_12_configs",
    output_prefix: str = "zentra_final_12_configs",
    staging_prefix: str = "zentra_phase2_staging",
):
    """
    Function 3:
    Merge generated 12 configuration CSVs into existing final CSVs, then upload
    the updated final files to S3.

    Input:
      zentra_phase2_staging/generated_12_configs/*.csv

    Output:
      zentra_final_12_configs/*.csv
      zentra_final_12_configs/_mapping_used.csv
      zentra_final_12_configs/_build_summary.json
    """
    upload_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_prefix": generated_prefix,
        "output_prefix": output_prefix,
        "uploaded_files": [],
    }

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"

        generated_df = _download_csv(generated_prefix, filename, f"generated_{filename}")
        generated_rows = int(len(generated_df))

        existing_df = None
        existing_rows = 0
        try:
            existing_df = _download_csv(output_prefix, filename, f"existing_{filename}")
            existing_rows = int(len(existing_df))
            faasr_log(f"Existing final {filename} found with {existing_rows} rows")
        except Exception as exc:
            faasr_log(f"No existing final {filename}; creating it fresh. Detail: {exc}")

        final_df = _merge_existing_and_generated(existing_df, generated_df)
        final_rows = int(len(final_df))

        final_df.to_csv(filename, index=False)
        faasr_put_file(local_file=filename, remote_folder=output_prefix, remote_file=filename)

        upload_summary["uploaded_files"].append({
            "file": filename,
            "existing_rows_before_update": existing_rows,
            "generated_new_rows_before_dedupe": generated_rows,
            "final_rows_after_update": final_rows,
            "rows_added_after_dedupe": max(final_rows - existing_rows, 0),
            "remote_path": f"{output_prefix}/{filename}",
            "measurement_counts": _measurement_counts(final_df),
        })
        faasr_log(
            f"Uploaded final {output_prefix}/{filename}: "
            f"existing={existing_rows}, generated={generated_rows}, final={final_rows}"
        )

    # Also publish mapping and summary in the final folder.
    try:
        mapping_df = _download_csv(staging_prefix, "mapping_used.csv", "_mapping_used.csv")
        mapping_df.to_csv("_mapping_used.csv", index=False)
        faasr_put_file(local_file="_mapping_used.csv", remote_folder=output_prefix, remote_file="_mapping_used.csv")
    except Exception as exc:
        faasr_log(f"Could not publish mapping file: {exc}")

    try:
        faasr_get_file(
            local_file="_build_summary.json",
            remote_folder=generated_prefix,
            remote_file="build_summary.json",
        )
        faasr_put_file(
            local_file="_build_summary.json",
            remote_folder=output_prefix,
            remote_file="_build_summary.json",
        )
    except Exception as exc:
        faasr_log(f"Could not publish build summary: {exc}")

    _put_json(upload_summary, output_prefix, "_upload_summary.json")
    faasr_log("Function 3 complete: final 12 configuration CSVs uploaded.")
