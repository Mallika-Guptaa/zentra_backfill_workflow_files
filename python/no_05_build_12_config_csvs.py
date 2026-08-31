import json
import re
import time
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
MAPPING_VERSION = "orchardgrass-v2-2026-07-22-z6-19598-port5-swd"
PORT5_SWD_START_UTC = pd.Timestamp("2025-08-21T00:00:00-07:00").tz_convert("UTC")

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
    ("S_W_D", "S", "Solar Array", "W", "West", "D", "Deficit (50% of daily evapotranspiration)", "NEWAg #5", "z6-19598", 5, "Port 5 (sensor description to confirm)", "Added to S_W_D from 2025-08-21 onward."),

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
    mapping = pd.DataFrame(MAPPING_ROWS, columns=MAPPING_COLUMNS)
    mapping["logger_serial_number"] = mapping["logger_serial_number"].astype(str).str.strip()
    mapping["port_num"] = pd.to_numeric(mapping["port_num"], errors="coerce").astype("Int64")
    return mapping


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _remote_folder_and_file(default_folder: str, obj: str) -> tuple[str, str]:
    obj = str(obj).strip().lstrip("/")
    if "/" in obj:
        return "/".join(obj.split("/")[:-1]), obj.split("/")[-1]
    return default_folder.strip().rstrip("/"), obj


def _extract_serial_from_source_path(source_path: str) -> str | None:
    """
    Raw Zentra CSVs do NOT contain logger_serial_number as a column.

    We derive it from the S3 path or filename, for example:
      zentra_raw_backfill/z6-19594/zentra_z6-19594_ports-2-3.csv
      zentra_z6-19594_ports-2-3.csv

    Returns:
      z6-19594, z6-19600, etc.
    """
    match = re.search(r"z6-\d+", str(source_path))
    return match.group(0) if match else None


def _list_csv_files(folder: str, max_files: str = "ALL") -> list[tuple[str, str, str]]:
    """
    List CSV files under a remote S3/FaaSr folder.

    Returns tuples:
      (remote_folder, remote_file, source_path)
    """
    folder = folder.strip().rstrip("/")
    faasr_log(f"Listing CSV files under {folder}")

    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=folder))
    except Exception as exc:
        faasr_log(f"No files found or could not list folder {folder}: {exc}")
        return []

    files = []
    for obj in objects:
        obj = str(obj).strip().lstrip("/")
        if not obj.lower().endswith(".csv"):
            continue

        # Only keep objects that are actually inside the requested folder.
        if "/" in obj and not obj.startswith(folder + "/"):
            continue

        remote_folder, remote_file = _remote_folder_and_file(folder, obj)
        source_path = f"{remote_folder}/{remote_file}"
        files.append((remote_folder, remote_file, source_path))

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


def _s3_object_exists(remote_folder: str, remote_file: str) -> bool:
    """
    Safely check whether an object exists before calling faasr_get_file.

    FaaSr's faasr_get_file can terminate the whole action if the object is missing,
    so missing files must be detected with faasr_get_folder_list first.
    """
    remote_folder = remote_folder.strip().rstrip("/")
    key = f"{remote_folder}/{remote_file}" if remote_folder else remote_file

    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=key))
    except Exception as exc:
        faasr_log(f"Could not check S3 object existence for {key}: {exc}")
        return False

    for obj in objects:
        obj = str(obj).strip().lstrip("/")
        if obj == key or obj.endswith(f"/{remote_file}") or obj == remote_file:
            return True

    return False


def _standardize_raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize raw Zentra data while preserving the original long format.

    Expected raw columns include:
      timestamp_utc, tz_offset, datetime, mrid, measurement, value, units,
      precision, port_num, sub_sensor_index, sensor_sn, sensor_name,
      error_flag, error_description, sensor_meta_errors

    The important matching columns are:
      logger_serial_number + port_num
    """
    df = df.copy()

    # Normalize common port column variants.
    if "port_num" not in df.columns:
        for candidate in ["port_number", "port", "Port", "PORT"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "port_num"})
                break

    # Normalize common datetime variants.
    if "datetime" not in df.columns:
        for candidate in ["timestamp", "Timestamp", "date_time", "time", "DateTime"]:
            if candidate in df.columns:
                df = df.rename(columns={candidate: "datetime"})
                break

    # Ensure raw columns exist even if a file is missing one.
    raw_columns = [
        "timestamp_utc",
        "tz_offset",
        "datetime",
        "mrid",
        "measurement",
        "value",
        "units",
        "precision",
        "port_num",
        "sub_sensor_index",
        "sensor_sn",
        "sensor_name",
        "error_flag",
        "error_description",
        "sensor_meta_errors",
    ]
    for col in raw_columns:
        if col not in df.columns:
            df[col] = pd.NA

    df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").astype("Int64")
    df["timestamp_utc"] = pd.to_numeric(df["timestamp_utc"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=True)

    return df


def _prepare_raw_df(df: pd.DataFrame, serial: str, source_path: str, allowed_ports: set[int]) -> pd.DataFrame:
    """
    Raw CSV does not contain logger_serial_number.

    We create logger_serial_number from the S3 folder/path or filename.
    The raw CSV's own port_num column is preserved and used for mapping.

    Final matching key:
      derived logger_serial_number + raw CSV port_num
    """
    df = _standardize_raw_columns(df)

    serial_from_path = _extract_serial_from_source_path(source_path)
    serial_for_rows = serial_from_path or str(serial).strip()

    if serial_from_path and serial_from_path != str(serial).strip():
        faasr_log(
            f"Warning: loop serial {serial} differs from serial parsed from path "
            f"{serial_from_path} for {source_path}. Using parsed path serial."
        )

    df["logger_serial_number"] = serial_for_rows
    df["source_file"] = source_path

    if allowed_ports:
        df = df[df["port_num"].isin(list(allowed_ports))].copy()

    keep_cols = [
        "logger_serial_number",
        "port_num",
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
    for col in keep_cols:
        if col not in df.columns:
            df[col] = pd.NA

    return df[keep_cols]


def _dedupe_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """
    Preserve every row from each raw source file.

    The previous implementation removed matching rows across overlapping raw
    files. That made a source file's filtered row count differ from its
    contribution to the final 12 CSVs. The new implementation only sorts.

    Duplicate prevention is handled at the source-file level by the processing
    manifest: a raw filename is processed once unless it is deliberately
    rebuilt or reprocessed.
    """
    if df.empty:
        return df.reset_index(drop=True)

    sort_cols = [
        c for c in [
            "configuration_code",
            "logger_serial_number",
            "port_num",
            "timestamp_utc",
            "datetime",
            "measurement",
            "sub_sensor_index",
            "source_file",
        ] if c in df.columns
    ]

    if sort_cols:
        df = df.sort_values(sort_cols, kind="stable")

    return df.reset_index(drop=True)


def _measurement_counts(df: pd.DataFrame) -> list[dict[str, Any]]:
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


def _load_processing_manifest(
    manifest_prefix: str,
    manifest_file: str,
) -> dict:
    if not _s3_object_exists(manifest_prefix, manifest_file):
        return {}

    local = f"_download_{manifest_file}"
    faasr_get_file(
        local_file=local,
        remote_folder=manifest_prefix,
        remote_file=manifest_file,
    )
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_source_record(
    mapping: pd.DataFrame,
    serial: str,
) -> dict:
    selected = mapping[
        mapping["logger_serial_number"].astype(str) == str(serial)
    ]

    targets = []
    for config_code, group in selected.groupby("configuration_code"):
        targets.append({
            "configuration_code": str(config_code),
            "ports": sorted(
                group["port_num"].dropna().astype(int).unique().tolist()
            ),
        })

    return {
        "serial": serial,
        "expected_targets": targets,
        "mapping_version": MAPPING_VERSION,
    }


def _load_existing_source_files(output_prefix: str) -> dict[str, set[str]]:
    """
    Safe incremental helper.

    Reads existing final 12 CSVs and records source_file values already included.
    If a final CSV does not exist, it is treated as empty without crashing.
    """
    existing_sources: dict[str, set[str]] = {code: set() for code in CONFIG_CODES}

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"

        if not _s3_object_exists(output_prefix, filename):
            faasr_log(f"No existing final file for {config_code}; treating as first build.")
            continue

        try:
            df_existing = _download_csv(output_prefix, filename, f"_existing_sources_{filename}")
        except Exception as exc:
            faasr_log(f"Could not read existing final file {filename}; treating as empty. Detail: {exc}")
            continue

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

    return existing_sources


def _raw_source_needed_for_serial(
    mapping: pd.DataFrame,
    serial: str,
    source_path: str,
    existing_sources_by_config: dict[str, set[str]],
) -> bool:
    """
    In incremental mode, stage a raw source file if at least one config that
    uses this serial does not yet contain that exact source_file.
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
        return False

    for config_code in config_codes:
        if source_path not in existing_sources_by_config.get(config_code, set()):
            return True

    return False


def _merge_existing_and_generated(existing_df: pd.DataFrame | None, generated_df: pd.DataFrame) -> pd.DataFrame:
    pieces = []

    if existing_df is not None and not existing_df.empty:
        pieces.append(existing_df)

    if generated_df is not None and not generated_df.empty:
        pieces.append(generated_df)

    if pieces:
        merged = pd.concat(pieces, ignore_index=True)
        merged = _standardize_raw_columns(merged)

        # Keep metadata columns from mapping if already present.
        for col in MAPPING_COLUMNS:
            if col not in merged.columns:
                merged[col] = pd.NA

        if "source_file" not in merged.columns:
            merged["source_file"] = pd.NA

        merged = _dedupe_and_sort(merged)
    else:
        merged = pd.DataFrame(columns=OUTPUT_COLUMNS)

    for col in OUTPUT_COLUMNS:
        if col not in merged.columns:
            merged[col] = pd.NA

    extra_cols = [c for c in merged.columns if c not in OUTPUT_COLUMNS]
    return merged[OUTPUT_COLUMNS + extra_cols].reset_index(drop=True)


def read_zentra_raw_files(
    raw_prefix: str = "zentra_raw_backfill",
    staging_prefix: str = "zentra_phase2_staging_manifest_v2",
    max_files_per_serial: str = "ALL",
    output_prefix: str = "zentra_final_12_configs",
    rebuild_mode: str = "incremental",
    manifest_prefix: str = "zentra_processing_state",
    manifest_file: str = "processed_raw_manifest.json",
):
    """
    Read raw Zentra CSVs and stage rows by logger.

    Source of truth:
      All CSV filenames currently present under
      zentra_raw_backfill/<serial>/.

    full:
      Stage every current raw file and rebuild all outputs.

    incremental:
      Compare the complete current raw filename inventory against the separate
      processing manifest. Stage only filenames that are not yet processed.

    Deleted raw files:
      Filenames present in the old manifest but absent from the current S3
      inventory are recorded. The upload function removes their rows from the
      final 12 CSVs.

    Mapping changes:
      Incremental mode refuses to continue when the manifest was created with a
      different mapping version. Run one full rebuild + overwrite first.
    """
    raw_prefix = raw_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    manifest_prefix = manifest_prefix.strip().rstrip("/")
    rebuild_mode = str(rebuild_mode).strip().lower()

    if rebuild_mode not in {"full", "incremental"}:
        raise ValueError("rebuild_mode must be 'full' or 'incremental'.")

    mapping = _mapping_df()
    serials = sorted(
        mapping["logger_serial_number"].dropna().astype(str).unique().tolist()
    )

    previous_manifest = _load_processing_manifest(
        manifest_prefix=manifest_prefix,
        manifest_file=manifest_file,
    )
    previous_processed = previous_manifest.get("processed_files", {})
    previous_version = previous_manifest.get("mapping_version")

    if (
        rebuild_mode == "incremental"
        and previous_processed
        and previous_version != MAPPING_VERSION
    ):
        raise RuntimeError(
            "The mapping changed. Run one full rebuild with "
            "rebuild_mode='full' and upload_mode='overwrite' before "
            "resuming incremental updates."
        )

    mapping_file = "mapping_used.csv"
    mapping.to_csv(mapping_file, index=False)
    faasr_put_file(
        local_file=mapping_file,
        remote_folder=staging_prefix,
        remote_file=mapping_file,
    )

    current_inventory: dict[str, dict[str, Any]] = {}
    successfully_staged_sources: list[str] = []
    serial_summary: dict[str, Any] = {}

    for serial in serials:
        raw_folder = f"{raw_prefix}/{serial}"
        allowed_ports = set(
            mapping[mapping["logger_serial_number"] == serial]["port_num"]
            .dropna()
            .astype(int)
            .tolist()
        )

        raw_files = _list_csv_files(
            raw_folder,
            max_files=max_files_per_serial,
        )

        for _, _, source_path in raw_files:
            current_inventory[source_path] = _build_source_record(
                mapping=mapping,
                serial=serial,
            )

        if rebuild_mode == "full":
            needed_raw_files = raw_files
        else:
            needed_raw_files = [
                item
                for item in raw_files
                if item[2] not in previous_processed
            ]

        pieces = []
        file_details = []

        for i, (remote_folder, remote_file, source_path) in enumerate(
            needed_raw_files
        ):
            try:
                raw_df = _download_csv(
                    remote_folder,
                    remote_file,
                    f"raw_{serial}_{i}.csv",
                )
                prepared = _prepare_raw_df(
                    df=raw_df,
                    serial=serial,
                    source_path=source_path,
                    allowed_ports=allowed_ports,
                )

                if not prepared.empty:
                    pieces.append(prepared)

                successfully_staged_sources.append(source_path)
                file_details.append({
                    "source_file": source_path,
                    "raw_rows": int(len(raw_df)),
                    "rows_after_port_filter": int(len(prepared)),
                    "rows_by_port": (
                        prepared.groupby("port_num", dropna=False)
                        .size()
                        .reset_index(name="rows")
                        .to_dict(orient="records")
                        if not prepared.empty
                        else []
                    ),
                })
            except Exception as exc:
                faasr_log(
                    f"Skipping unreadable raw file {source_path}: {exc}"
                )

        combined = (
            pd.concat(pieces, ignore_index=True)
            if pieces
            else pd.DataFrame()
        )
        combined = _dedupe_and_sort(combined)

        staged_file = f"{serial}_raw_combined.csv"
        combined.to_csv(staged_file, index=False)
        faasr_put_file(
            local_file=staged_file,
            remote_folder=f"{staging_prefix}/raw_by_serial",
            remote_file=staged_file,
        )

        serial_summary[serial] = {
            "allowed_ports_from_mapping": sorted(allowed_ports),
            "raw_files_found": len(raw_files),
            "raw_files_staged": len(needed_raw_files),
            "raw_files_skipped_as_already_processed": (
                len(raw_files) - len(needed_raw_files)
            ),
            "staged_rows_after_port_filter": int(len(combined)),
            "staged_file_details": file_details,
            "measurement_counts": _measurement_counts(combined),
        }

        faasr_log(
            f"{serial}: found={len(raw_files)}, "
            f"staged={len(needed_raw_files)}, "
            f"rows={len(combined)}, "
            f"allowed_ports={sorted(allowed_ports)}"
        )

    current_sources = set(current_inventory)
    previous_sources = set(previous_processed)
    deleted_sources = sorted(previous_sources - current_sources)

    if rebuild_mode == "full":
        candidate_processed = {}
    else:
        candidate_processed = {
            path: details
            for path, details in previous_processed.items()
            if path in current_sources
        }

    now_utc = datetime.now(timezone.utc).isoformat()
    for source_path in successfully_staged_sources:
        record = current_inventory.get(
            source_path,
            {
                "serial": _extract_serial_from_source_path(source_path),
                "expected_targets": [],
            },
        )
        candidate_processed[source_path] = {
            **record,
            "processed_at_utc": now_utc,
            "mapping_version": MAPPING_VERSION,
        }

    # In a full rebuild, every successfully readable current file is committed.
    if rebuild_mode == "full":
        candidate_processed = {
            path: {
                **current_inventory[path],
                "processed_at_utc": now_utc,
                "mapping_version": MAPPING_VERSION,
            }
            for path in successfully_staged_sources
        }

    processing_plan = {
        "created_at_utc": now_utc,
        "mapping_version": MAPPING_VERSION,
        "rebuild_mode": rebuild_mode,
        "raw_prefix": raw_prefix,
        "output_prefix": output_prefix,
        "manifest_prefix": manifest_prefix,
        "manifest_file": manifest_file,
        "current_inventory_count": len(current_sources),
        "previous_processed_count": len(previous_sources),
        "new_or_reprocessed_sources": sorted(
            set(successfully_staged_sources)
        ),
        "deleted_sources": deleted_sources,
        "serials": serial_summary,
    }

    candidate_manifest = {
        "updated_at_utc": now_utc,
        "mapping_version": MAPPING_VERSION,
        "raw_prefix": raw_prefix,
        "processed_files": candidate_processed,
    }

    _put_json(
        processing_plan,
        staging_prefix,
        "processing_plan.json",
    )
    _put_json(
        candidate_manifest,
        staging_prefix,
        "candidate_processed_manifest.json",
    )
    _put_json(
        {
            "created_at_utc": now_utc,
            "mapping_version": MAPPING_VERSION,
            "serials": serial_summary,
        },
        staging_prefix,
        "raw_manifest.json",
    )

    faasr_log(
        "Function 1 complete: current raw filename inventory compared "
        "against the processing manifest."
    )



# ---------------------------------------------------------------------
# PARALLEL FULL-REBUILD HELPERS
# ---------------------------------------------------------------------
#
# These functions keep raw_by_serial for debugging, but build one logger
# per FaaSr action so the expensive historical S3 reads happen in parallel.
#
# Workflow:
#   initialize_parallel_raw_build
#       -> 13 x build_raw_by_serial  (parallel)
#       -> wait_for_raw_by_serial    (barrier/poller, started in parallel)
#       -> form_12_config_csvs
#       -> upload_12_config_csvs
#
# The run marker prevents stale completion markers from an older run from
# being accepted by the barrier.
#

PARALLEL_SERIALS = [
    "z6-19600",
    "z6-12196",
    "z6-19602",
    "z6-19604",
    "z6-19597",
    "z6-19594",
    "z6-19599",
    "z6-12197",
    "z6-19595",
    "z6-19598",
    "z6-12202",
    "z6-19596",
    "z6-19603",
]


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_serial_list(value: Any) -> list[str]:
    if value is None or str(value).strip().upper() == "ALL":
        return list(PARALLEL_SERIALS)
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text_value = str(value).strip()
    if text_value.startswith("["):
        parsed = json.loads(text_value)
        return [str(x).strip() for x in parsed if str(x).strip()]
    return [x.strip() for x in text_value.split(",") if x.strip()]


def initialize_parallel_raw_build(
    raw_prefix: str = "zentra_raw_backfill",
    staging_prefix: str = "zentra_phase2_staging_manifest_v2_full_parallel",
    output_prefix: str = "zentra_final_12_configs",
    rebuild_mode: str = "full",
    manifest_prefix: str = "zentra_processing_state",
    manifest_file: str = "processed_raw_manifest.json",
):
    """
    Initialize a parallel full rebuild.

    This function is intentionally lightweight:
      1. writes mapping_used.csv once
      2. writes a unique run marker
      3. returns, allowing FaaSr to fan out to all logger workers + barrier

    The run marker is essential because raw_by_serial status marker filenames
    are stable between runs. The barrier accepts only markers whose run_id
    matches the current run marker.
    """
    raw_prefix = raw_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    manifest_prefix = manifest_prefix.strip().rstrip("/")
    rebuild_mode = str(rebuild_mode).strip().lower()

    if rebuild_mode != "full":
        raise ValueError(
            "initialize_parallel_raw_build is for a full rebuild. "
            "Use rebuild_mode='full'."
        )

    mapping = _mapping_df()

    mapping_file = "mapping_used.csv"
    mapping.to_csv(mapping_file, index=False)
    faasr_put_file(
        local_file=mapping_file,
        remote_folder=staging_prefix,
        remote_file=mapping_file,
    )

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")

    run_marker = {
        "run_id": run_id,
        "created_at_utc": now.isoformat(),
        "mapping_version": MAPPING_VERSION,
        "raw_prefix": raw_prefix,
        "staging_prefix": staging_prefix,
        "output_prefix": output_prefix,
        "rebuild_mode": rebuild_mode,
        "manifest_prefix": manifest_prefix,
        "manifest_file": manifest_file,
        "expected_serials": PARALLEL_SERIALS,
    }

    _put_json(
        run_marker,
        staging_prefix,
        "_parallel_run_marker.json",
    )

    faasr_log(
        f"Parallel raw build initialized. run_id={run_id}; "
        f"expected_serials={len(PARALLEL_SERIALS)}"
    )


def build_raw_by_serial(
    serial_number: str,
    raw_prefix: str = "zentra_raw_backfill",
    staging_prefix: str = "zentra_phase2_staging_manifest_v2_full_parallel",
    max_files_per_serial: str = "ALL",
    fail_on_unreadable: Any = True,
):
    """
    Build exactly ONE raw_by_serial CSV.

    Each FaaSr action calls this function with a different serial_number.
    Therefore the 13 expensive historical reads can execute concurrently.

    Output:
      <staging_prefix>/raw_by_serial/<serial>_raw_combined.csv

    Completion marker:
      <staging_prefix>/raw_by_serial_status/<serial>.json

    Safety:
      - completion marker includes current run_id
      - by default, ANY unreadable raw CSV makes this worker fail
      - partial results are not accepted by the barrier as success
    """
    serial = str(serial_number).strip()
    raw_prefix = raw_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    fail_on_unreadable = _truthy(fail_on_unreadable)

    if serial not in PARALLEL_SERIALS:
        raise ValueError(
            f"Unknown serial_number={serial}. "
            f"Expected one of {PARALLEL_SERIALS}"
        )

    run_marker = _load_processing_manifest(
        staging_prefix,
        "_parallel_run_marker.json",
    )
    if not run_marker:
        raise RuntimeError(
            "Missing _parallel_run_marker.json. "
            "initialize_parallel_raw_build must run first."
        )

    run_id = str(run_marker.get("run_id", "")).strip()
    if not run_id:
        raise RuntimeError("Current parallel run marker has no run_id.")

    if run_marker.get("mapping_version") != MAPPING_VERSION:
        raise RuntimeError(
            "Current run marker mapping version does not match code."
        )

    mapping = _mapping_df()
    selected_mapping = mapping[
        mapping["logger_serial_number"].astype(str) == serial
    ].copy()

    if selected_mapping.empty:
        raise RuntimeError(f"No mapping rows found for {serial}.")

    allowed_ports = set(
        selected_mapping["port_num"]
        .dropna()
        .astype(int)
        .tolist()
    )

    raw_folder = f"{raw_prefix}/{serial}"
    raw_files = _list_csv_files(
        raw_folder,
        max_files=max_files_per_serial,
    )

    pieces = []
    file_details = []
    successfully_staged_sources = []
    unreadable_files = []

    faasr_log(
        f"{serial}: starting parallel raw build; "
        f"raw_files={len(raw_files)}, allowed_ports={sorted(allowed_ports)}, "
        f"run_id={run_id}"
    )

    for i, (remote_folder, remote_file, source_path) in enumerate(raw_files):
        try:
            raw_df = _download_csv(
                remote_folder,
                remote_file,
                f"raw_{serial}_{i}.csv",
            )
            prepared = _prepare_raw_df(
                df=raw_df,
                serial=serial,
                source_path=source_path,
                allowed_ports=allowed_ports,
            )

            if not prepared.empty:
                pieces.append(prepared)

            successfully_staged_sources.append(source_path)
            file_details.append({
                "source_file": source_path,
                "raw_rows": int(len(raw_df)),
                "rows_after_port_filter": int(len(prepared)),
                "rows_by_port": (
                    prepared.groupby("port_num", dropna=False)
                    .size()
                    .reset_index(name="rows")
                    .to_dict(orient="records")
                    if not prepared.empty
                    else []
                ),
            })

            if (i + 1) % 100 == 0:
                faasr_log(
                    f"{serial}: processed {i + 1}/{len(raw_files)} raw files"
                )

        except Exception as exc:
            unreadable_files.append({
                "source_file": source_path,
                "error": str(exc),
            })
            faasr_log(
                f"{serial}: unreadable raw file {source_path}: {exc}"
            )

    combined = (
        pd.concat(pieces, ignore_index=True)
        if pieces
        else pd.DataFrame()
    )
    combined = _dedupe_and_sort(combined)

    staged_file = f"{serial}_raw_combined.csv"
    combined.to_csv(staged_file, index=False)
    faasr_put_file(
        local_file=staged_file,
        remote_folder=f"{staging_prefix}/raw_by_serial",
        remote_file=staged_file,
    )

    worker_status = "success"
    if unreadable_files and fail_on_unreadable:
        worker_status = "failed"

    completion_marker = {
        "run_id": run_id,
        "mapping_version": MAPPING_VERSION,
        "status": worker_status,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "serial": serial,
        "raw_folder": raw_folder,
        "allowed_ports_from_mapping": sorted(allowed_ports),
        "raw_files_found": len(raw_files),
        "raw_files_staged_successfully": len(successfully_staged_sources),
        "unreadable_file_count": len(unreadable_files),
        "unreadable_files": unreadable_files,
        "staged_rows_after_port_filter": int(len(combined)),
        "staged_path": (
            f"{staging_prefix}/raw_by_serial/{staged_file}"
        ),
        "successfully_staged_sources": successfully_staged_sources,
        "expected_targets": _build_source_record(
            mapping=mapping,
            serial=serial,
        ).get("expected_targets", []),
        "measurement_counts": _measurement_counts(combined),
        "file_details": file_details,
    }

    _put_json(
        completion_marker,
        f"{staging_prefix}/raw_by_serial_status",
        f"{serial}.json",
    )

    faasr_log(
        f"{serial}: parallel raw build finished with "
        f"status={worker_status}; rows={len(combined)}; "
        f"files_ok={len(successfully_staged_sources)}; "
        f"files_failed={len(unreadable_files)}"
    )

    if worker_status != "success":
        raise RuntimeError(
            f"{serial}: {len(unreadable_files)} raw file(s) could not be read. "
            "Worker marked failed; final 12-config build will not proceed."
        )


def wait_for_raw_by_serial(
    staging_prefix: str = "zentra_phase2_staging_manifest_v2_full_parallel",
    raw_prefix: str = "zentra_raw_backfill",
    output_prefix: str = "zentra_final_12_configs",
    manifest_prefix: str = "zentra_processing_state",
    manifest_file: str = "processed_raw_manifest.json",
    expected_serials: Any = "ALL",
    poll_seconds: int = 30,
    timeout_seconds: int = 14400,
):
    """
    Barrier for the 13 parallel logger workers.

    It waits until every expected serial has a SUCCESS marker for the CURRENT
    run_id. Stale markers from prior runs are ignored.

    After all workers succeed, this function creates:
      processing_plan.json
      candidate_processed_manifest.json
      raw_manifest.json

    Those are the same control files expected by upload_12_config_csvs().
    """
    staging_prefix = staging_prefix.strip().rstrip("/")
    raw_prefix = raw_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    manifest_prefix = manifest_prefix.strip().rstrip("/")
    expected = _parse_serial_list(expected_serials)

    run_marker = _load_processing_manifest(
        staging_prefix,
        "_parallel_run_marker.json",
    )
    if not run_marker:
        raise RuntimeError(
            "Missing current _parallel_run_marker.json."
        )

    run_id = str(run_marker.get("run_id", "")).strip()
    if not run_id:
        raise RuntimeError("Parallel run marker has no run_id.")

    if run_marker.get("mapping_version") != MAPPING_VERSION:
        raise RuntimeError(
            "Run marker mapping version does not match code."
        )

    started = time.time()
    last_logged_missing = None
    markers: dict[str, dict] = {}

    while True:
        markers = {}
        missing = []
        failed = []

        for serial in expected:
            marker = _load_processing_manifest(
                f"{staging_prefix}/raw_by_serial_status",
                f"{serial}.json",
            )

            if not marker:
                missing.append(serial)
                continue

            # Ignore stale completion marker from an older rebuild.
            if str(marker.get("run_id", "")) != run_id:
                missing.append(serial)
                continue

            status = str(marker.get("status", "")).strip().lower()
            if status == "failed":
                failed.append(serial)
                markers[serial] = marker
                continue

            if status != "success":
                missing.append(serial)
                continue

            markers[serial] = marker

        if failed:
            details = {
                serial: markers[serial].get("unreadable_files", [])
                for serial in failed
            }
            raise RuntimeError(
                "Parallel raw build failed for serial(s): "
                f"{failed}. Details: {details}"
            )

        if not missing:
            break

        elapsed = int(time.time() - started)
        if elapsed >= int(timeout_seconds):
            raise TimeoutError(
                "Timed out waiting for parallel raw_by_serial workers. "
                f"run_id={run_id}; still_missing={missing}; "
                f"elapsed_seconds={elapsed}"
            )

        missing_key = tuple(sorted(missing))
        if missing_key != last_logged_missing:
            faasr_log(
                f"Waiting for raw_by_serial workers. "
                f"run_id={run_id}; complete={len(expected) - len(missing)}/"
                f"{len(expected)}; missing={missing}"
            )
            last_logged_missing = missing_key

        time.sleep(max(5, int(poll_seconds)))

    # All current-run workers succeeded.
    previous_manifest = _load_processing_manifest(
        manifest_prefix,
        manifest_file,
    )
    previous_processed = previous_manifest.get("processed_files", {})

    mapping = _mapping_df()
    now_utc = datetime.now(timezone.utc).isoformat()

    successfully_staged_sources = []
    candidate_processed = {}
    serial_summary = {}

    for serial in expected:
        marker = markers[serial]
        sources = marker.get("successfully_staged_sources", [])
        successfully_staged_sources.extend(sources)

        serial_summary[serial] = {
            "allowed_ports_from_mapping": marker.get(
                "allowed_ports_from_mapping", []
            ),
            "raw_files_found": marker.get("raw_files_found", 0),
            "raw_files_staged": marker.get(
                "raw_files_staged_successfully", 0
            ),
            "unreadable_file_count": marker.get(
                "unreadable_file_count", 0
            ),
            "staged_rows_after_port_filter": marker.get(
                "staged_rows_after_port_filter", 0
            ),
            "staged_path": marker.get("staged_path"),
            "measurement_counts": marker.get(
                "measurement_counts", []
            ),
        }

        source_record = _build_source_record(
            mapping=mapping,
            serial=serial,
        )

        for source_path in sources:
            candidate_processed[source_path] = {
                **source_record,
                "processed_at_utc": now_utc,
                "mapping_version": MAPPING_VERSION,
            }

    current_sources = set(successfully_staged_sources)
    previous_sources = set(previous_processed)
    deleted_sources = sorted(previous_sources - current_sources)

    processing_plan = {
        "created_at_utc": now_utc,
        "run_id": run_id,
        "mapping_version": MAPPING_VERSION,
        "rebuild_mode": "full",
        "raw_prefix": raw_prefix,
        "output_prefix": output_prefix,
        "manifest_prefix": manifest_prefix,
        "manifest_file": manifest_file,
        "current_inventory_count": len(current_sources),
        "previous_processed_count": len(previous_sources),
        "new_or_reprocessed_sources": sorted(current_sources),
        "deleted_sources": deleted_sources,
        "serials": serial_summary,
        "parallel_workers": len(expected),
    }

    candidate_manifest = {
        "updated_at_utc": now_utc,
        "run_id": run_id,
        "mapping_version": MAPPING_VERSION,
        "raw_prefix": raw_prefix,
        "processed_files": candidate_processed,
    }

    raw_manifest = {
        "created_at_utc": now_utc,
        "run_id": run_id,
        "mapping_version": MAPPING_VERSION,
        "parallel_workers": len(expected),
        "serials": serial_summary,
    }

    _put_json(
        processing_plan,
        staging_prefix,
        "processing_plan.json",
    )
    _put_json(
        candidate_manifest,
        staging_prefix,
        "candidate_processed_manifest.json",
    )
    _put_json(
        raw_manifest,
        staging_prefix,
        "raw_manifest.json",
    )

    faasr_log(
        f"All {len(expected)} raw_by_serial workers completed successfully "
        f"for run_id={run_id}. Barrier complete."
    )

def form_12_config_csvs(
    staging_prefix: str = "zentra_phase2_staging",
    generated_prefix: str = "zentra_phase2_staging/generated_12_configs",
):
    """
    Function 2:
    Form 12 final configuration CSVs by exact matching:

      mapping.logger_serial_number == raw.logger_serial_number
      AND
      mapping.port_num == raw.port_num

    This is the key correction. No raw row can enter a configuration unless
    BOTH logger serial number and port number match the mapping.
    """
    staging_prefix = staging_prefix.strip().rstrip("/")
    generated_prefix = generated_prefix.strip().rstrip("/")

    mapping = _download_csv(staging_prefix, "mapping_used.csv", "mapping_used.csv")
    mapping["logger_serial_number"] = mapping["logger_serial_number"].astype(str).str.strip()
    mapping["port_num"] = pd.to_numeric(mapping["port_num"], errors="coerce").astype("Int64")

    raw_pieces = []

    for serial in sorted(mapping["logger_serial_number"].dropna().astype(str).unique().tolist()):
        staged_file = f"{serial}_raw_combined.csv"
        try:
            df_serial = _download_csv(
                f"{staging_prefix}/raw_by_serial",
                staged_file,
                f"staged_{serial}.csv",
            )
            df_serial = _standardize_raw_columns(df_serial)
            df_serial["logger_serial_number"] = df_serial["logger_serial_number"].astype(str).str.strip()
            df_serial["port_num"] = pd.to_numeric(df_serial["port_num"], errors="coerce").astype("Int64")
            raw_pieces.append(df_serial)
            faasr_log(f"Loaded staged raw for {serial}: {len(df_serial)} rows")
        except Exception as exc:
            faasr_log(f"No staged raw file for {serial}; using no rows. Error: {exc}")

    all_raw = pd.concat(raw_pieces, ignore_index=True) if raw_pieces else pd.DataFrame()

    if all_raw.empty:
        faasr_log("No staged raw rows found. Generated 12 empty CSVs.")
        merged_all = pd.DataFrame(columns=OUTPUT_COLUMNS)
    else:
        # Remove any accidental mapping metadata from raw before merging.
        raw_drop_cols = [
            c for c in [
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
            ] if c in all_raw.columns
        ]
        all_raw_clean = all_raw.drop(columns=raw_drop_cols)

        merged_all = mapping.merge(
            all_raw_clean,
            on=["logger_serial_number", "port_num"],
            how="inner",
            validate="many_to_many",
        )

        # z6-19598 port 5 belongs to S_W_D only from 2025-08-21 onward.
        special_port5 = (
            (merged_all["configuration_code"] == "S_W_D")
            & (merged_all["logger_serial_number"] == "z6-19598")
            & (merged_all["port_num"] == 5)
        )
        port5_date_ok = (
            merged_all["datetime"].notna()
            & (merged_all["datetime"] >= PORT5_SWD_START_UTC)
        )
        merged_all = merged_all[
            (~special_port5) | port5_date_ok
        ].copy()

        merged_all = _dedupe_and_sort(merged_all)

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mapping_version": MAPPING_VERSION,
        "generated_prefix": generated_prefix,
        "matching_rule": "exact inner join on logger_serial_number + port_num",
        "total_staged_raw_rows": int(len(all_raw)),
        "total_matched_rows": int(len(merged_all)),
        "configs": {},
    }

    for config_code in CONFIG_CODES:
        if merged_all.empty:
            config_df = pd.DataFrame(columns=OUTPUT_COLUMNS)
        else:
            config_df = merged_all[merged_all["configuration_code"] == config_code].copy()
            config_df = _dedupe_and_sort(config_df)

        for col in OUTPUT_COLUMNS:
            if col not in config_df.columns:
                config_df[col] = pd.NA

        extra_cols = [c for c in config_df.columns if c not in OUTPUT_COLUMNS]
        config_df = config_df[OUTPUT_COLUMNS + extra_cols]

        out_file = f"{config_code}.csv"
        config_df.to_csv(out_file, index=False)
        faasr_put_file(local_file=out_file, remote_folder=generated_prefix, remote_file=out_file)

        config_map = mapping[mapping["configuration_code"] == config_code]
        summary["configs"][config_code] = {
            "rows": int(len(config_df)),
            "generated_path": f"{generated_prefix}/{out_file}",
            "ports_used": config_map[
                ["logger_name", "logger_serial_number", "port_num", "port_description"]
            ].to_dict(orient="records"),
            "measurement_counts": _measurement_counts(config_df),
            "source_file_counts": (
                config_df.groupby(["source_file", "logger_serial_number", "port_num"], dropna=False)
                .size()
                .reset_index(name="rows")
                .to_dict(orient="records")
                if not config_df.empty
                else []
            ),
        }

        faasr_log(f"Generated {out_file}: {len(config_df)} rows")

    _put_json(summary, generated_prefix, "build_summary.json")
    faasr_log("Function 2 complete: 12 CSVs formed by exact serial+port matching.")


def upload_12_config_csvs(
    generated_prefix: str = (
        "zentra_phase2_staging_manifest_v2/generated_12_configs"
    ),
    output_prefix: str = "zentra_final_12_configs",
    staging_prefix: str = "zentra_phase2_staging_manifest_v2",
    upload_mode: str = "merge",
    manifest_prefix: str = "zentra_processing_state",
    manifest_file: str = "processed_raw_manifest.json",
):
    """
    Upload the generated 12 configuration CSVs.

    overwrite:
      Replace all final files. Use after a mapping change or when existing
      outputs are not trusted.

    merge:
      1. Remove rows belonging to raw source files regenerated in this run.
      2. Remove rows belonging to source files deleted from the raw S3 folder.
      3. Append the newly generated source rows.

    This makes the workflow idempotent at the raw filename level while
    preserving every row from each source file.
    """
    generated_prefix = generated_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    manifest_prefix = manifest_prefix.strip().rstrip("/")
    upload_mode = str(upload_mode).strip().lower()

    if upload_mode not in {"overwrite", "merge"}:
        raise ValueError("upload_mode must be 'overwrite' or 'merge'.")

    processing_plan = _load_processing_manifest(
        staging_prefix,
        "processing_plan.json",
    )
    candidate_manifest = _load_processing_manifest(
        staging_prefix,
        "candidate_processed_manifest.json",
    )

    if not processing_plan or not candidate_manifest:
        raise RuntimeError(
            "Missing processing_plan.json or "
            "candidate_processed_manifest.json in staging."
        )

    if processing_plan.get("mapping_version") != MAPPING_VERSION:
        raise RuntimeError(
            "The staging mapping version does not match the code."
        )

    regenerated_sources = set(
        processing_plan.get("new_or_reprocessed_sources", [])
    )
    deleted_sources = set(
        processing_plan.get("deleted_sources", [])
    )
    sources_to_remove = regenerated_sources | deleted_sources

    upload_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mapping_version": MAPPING_VERSION,
        "generated_prefix": generated_prefix,
        "output_prefix": output_prefix,
        "upload_mode": upload_mode,
        "regenerated_sources": sorted(regenerated_sources),
        "deleted_sources": sorted(deleted_sources),
        "uploaded_files": [],
    }

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"
        generated_df = _download_csv(
            generated_prefix,
            filename,
            f"generated_{filename}",
        )

        existing_df = None
        existing_rows = 0

        if (
            upload_mode == "merge"
            and _s3_object_exists(output_prefix, filename)
        ):
            existing_df = _download_csv(
                output_prefix,
                filename,
                f"existing_{filename}",
            )
            existing_rows = int(len(existing_df))

        if upload_mode == "overwrite":
            final_df = generated_df.copy()
        else:
            if existing_df is None:
                retained_existing = pd.DataFrame()
            else:
                retained_existing = existing_df.copy()
                if "source_file" not in retained_existing.columns:
                    retained_existing["source_file"] = pd.NA

                if sources_to_remove:
                    retained_existing = retained_existing[
                        ~retained_existing["source_file"]
                        .astype(str)
                        .isin(sources_to_remove)
                    ].copy()

            pieces = [
                df for df in [retained_existing, generated_df]
                if df is not None and not df.empty
            ]
            final_df = (
                pd.concat(pieces, ignore_index=True)
                if pieces
                else pd.DataFrame(columns=OUTPUT_COLUMNS)
            )

        for col in OUTPUT_COLUMNS:
            if col not in final_df.columns:
                final_df[col] = pd.NA

        # Keep the exact selected output schema. No cross-file row removal.
        final_df = _dedupe_and_sort(final_df[OUTPUT_COLUMNS])

        final_df.to_csv(filename, index=False)
        faasr_put_file(
            local_file=filename,
            remote_folder=output_prefix,
            remote_file=filename,
        )

        upload_summary["uploaded_files"].append({
            "file": filename,
            "existing_rows_before_update": existing_rows,
            "generated_rows": int(len(generated_df)),
            "final_rows_after_update": int(len(final_df)),
            "remote_path": f"{output_prefix}/{filename}",
            "measurement_counts": _measurement_counts(final_df),
            "source_file_counts": (
                final_df.groupby(
                    ["source_file", "logger_serial_number", "port_num"],
                    dropna=False,
                )
                .size()
                .reset_index(name="rows")
                .to_dict(orient="records")
                if not final_df.empty
                else []
            ),
        })

        faasr_log(
            f"Uploaded {filename}: existing={existing_rows}, "
            f"generated={len(generated_df)}, "
            f"final={len(final_df)}, mode={upload_mode}"
        )

    mapping_df = _download_csv(
        staging_prefix,
        "mapping_used.csv",
        "_mapping_used.csv",
    )
    mapping_df.to_csv("_mapping_used.csv", index=False)
    faasr_put_file(
        local_file="_mapping_used.csv",
        remote_folder=output_prefix,
        remote_file="_mapping_used.csv",
    )

    build_summary = _load_processing_manifest(
        generated_prefix,
        "build_summary.json",
    )
    _put_json(
        build_summary,
        output_prefix,
        "_build_summary.json",
    )
    _put_json(
        upload_summary,
        output_prefix,
        "_upload_summary.json",
    )

    # Commit the processed filename manifest only after all 12 outputs succeed.
    _put_json(
        candidate_manifest,
        manifest_prefix,
        manifest_file,
    )

    faasr_log(
        "Function 3 complete: outputs uploaded and processed raw-file "
        "manifest committed."
    )

