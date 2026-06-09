import json
import re
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
    if df.empty:
        return df.reset_index(drop=True)

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
            "logger_serial_number",
            "port_num",
            "timestamp_utc",
            "datetime",
            "measurement",
            "units",
        ] if c in df.columns
    ]

    if sort_cols:
        df = df.sort_values(sort_cols)

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
    staging_prefix: str = "zentra_phase2_staging",
    max_files_per_serial: str = "ALL",
    output_prefix: str = "zentra_final_12_configs",
    rebuild_mode: str = "incremental",
):
    """
    Function 1:
    Read raw Zentra CSV files from S3 and stage rows by logger.

    Critical correction:
      The raw data is matched using logger_serial_number + port_num.
      logger_serial_number is derived from the S3 folder/path or filename, because it is NOT a raw CSV column.
      port_num comes directly from the raw CSV column.

    rebuild_mode:
      - "full": stage all raw files and rebuild the 12 CSVs from scratch.
      - "incremental": stage only raw source files not already represented
        in the existing final 12 CSVs.
    """
    raw_prefix = raw_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    rebuild_mode = str(rebuild_mode).strip().lower()

    mapping = _mapping_df()
    serials = sorted(mapping["logger_serial_number"].dropna().astype(str).unique().tolist())

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_prefix": raw_prefix,
        "staging_prefix": staging_prefix,
        "output_prefix": output_prefix,
        "max_files_per_serial": max_files_per_serial,
        "rebuild_mode": rebuild_mode,
        "matching_rule": "derived logger_serial_number from S3 path/filename + raw CSV port_num",
        "serials": {},
    }

    mapping_file = "mapping_used.csv"
    mapping.to_csv(mapping_file, index=False)
    faasr_put_file(local_file=mapping_file, remote_folder=staging_prefix, remote_file=mapping_file)

    existing_sources_by_config = (
        {code: set() for code in CONFIG_CODES}
        if rebuild_mode == "full"
        else _load_existing_source_files(output_prefix)
    )

    for serial in serials:
        raw_folder = f"{raw_prefix}/{serial}"
        allowed_ports = set(
            mapping[mapping["logger_serial_number"] == serial]["port_num"]
            .dropna()
            .astype(int)
            .tolist()
        )

        raw_files = _list_csv_files(raw_folder, max_files=max_files_per_serial)

        if rebuild_mode == "full":
            needed_raw_files = raw_files
            skipped_already_processed = []
        else:
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
            f"{serial}: raw_files={len(raw_files)}, staged={len(needed_raw_files)}, "
            f"skipped={len(skipped_already_processed)}, allowed_ports={sorted(allowed_ports)}"
        )

        pieces = []
        for i, (remote_folder, remote_file, source_path) in enumerate(needed_raw_files):
            try:
                df = _download_csv(remote_folder, remote_file, f"raw_{serial}_{i}.csv")
                prepared = _prepare_raw_df(
                    df=df,
                    serial=serial,
                    source_path=source_path,
                    allowed_ports=allowed_ports,
                )
                if not prepared.empty:
                    pieces.append(prepared)
            except Exception as exc:
                faasr_log(f"Skipping unreadable raw file {source_path}: {exc}")

        combined = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
        combined = _dedupe_and_sort(combined)

        staged_file = f"{serial}_raw_combined.csv"
        combined.to_csv(staged_file, index=False)
        faasr_put_file(
            local_file=staged_file,
            remote_folder=f"{staging_prefix}/raw_by_serial",
            remote_file=staged_file,
        )

        manifest["serials"][serial] = {
            "allowed_ports_from_mapping": sorted(allowed_ports),
            "raw_files_found": len(raw_files),
            "raw_files_staged": len(needed_raw_files),
            "raw_files_skipped_already_processed": len(skipped_already_processed),
            "staged_rows_after_port_filter": int(len(combined)),
            "staged_path": f"{staging_prefix}/raw_by_serial/{staged_file}",
            "measurement_counts": _measurement_counts(combined),
        }

    _put_json(manifest, staging_prefix, "raw_manifest.json")
    faasr_log("Function 1 complete: raw files staged by logger_serial_number and port_num.")


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
        merged_all = _dedupe_and_sort(merged_all)

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
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
        }

        faasr_log(f"Generated {out_file}: {len(config_df)} rows")

    _put_json(summary, generated_prefix, "build_summary.json")
    faasr_log("Function 2 complete: 12 CSVs formed by exact serial+port matching.")


def upload_12_config_csvs(
    generated_prefix: str = "zentra_phase2_staging/generated_12_configs",
    output_prefix: str = "zentra_final_12_configs",
    staging_prefix: str = "zentra_phase2_staging",
    upload_mode: str = "merge",
):
    """
    Function 3:
    Upload the generated 12 configuration CSVs.

    upload_mode:
      - "overwrite": replace final CSVs completely with generated CSVs.
        Use this when previous final CSVs may be wrong.
      - "merge": merge generated rows into existing final CSVs.
        Use this for daily incremental updates after the final CSVs are correct.
    """
    generated_prefix = generated_prefix.strip().rstrip("/")
    output_prefix = output_prefix.strip().rstrip("/")
    staging_prefix = staging_prefix.strip().rstrip("/")
    upload_mode = str(upload_mode).strip().lower()

    upload_summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "generated_prefix": generated_prefix,
        "output_prefix": output_prefix,
        "upload_mode": upload_mode,
        "uploaded_files": [],
    }

    for config_code in CONFIG_CODES:
        filename = f"{config_code}.csv"

        generated_df = _download_csv(generated_prefix, filename, f"generated_{filename}")
        generated_rows = int(len(generated_df))

        if upload_mode == "overwrite":
            final_df = _merge_existing_and_generated(None, generated_df)
            existing_rows = 0
            faasr_log(f"Overwrite mode: replacing final {filename} with generated rows only.")
        else:
            existing_df = None
            existing_rows = 0

            if _s3_object_exists(output_prefix, filename):
                try:
                    existing_df = _download_csv(output_prefix, filename, f"existing_{filename}")
                    existing_rows = int(len(existing_df))
                    faasr_log(f"Existing final {filename} found with {existing_rows} rows")
                except Exception as exc:
                    faasr_log(f"Could not read existing final {filename}; using generated rows only. Detail: {exc}")
            else:
                faasr_log(f"No existing final {filename}; creating it fresh.")

            final_df = _merge_existing_and_generated(existing_df, generated_df)

        final_rows = int(len(final_df))

        final_df.to_csv(filename, index=False)
        faasr_put_file(local_file=filename, remote_folder=output_prefix, remote_file=filename)

        upload_summary["uploaded_files"].append({
            "file": filename,
            "existing_rows_before_update": existing_rows,
            "generated_rows": generated_rows,
            "final_rows_after_update": final_rows,
            "rows_added_after_dedupe": max(final_rows - existing_rows, 0),
            "remote_path": f"{output_prefix}/{filename}",
            "measurement_counts": _measurement_counts(final_df),
        })

        faasr_log(
            f"Uploaded final {output_prefix}/{filename}: "
            f"existing={existing_rows}, generated={generated_rows}, final={final_rows}, mode={upload_mode}"
        )

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
