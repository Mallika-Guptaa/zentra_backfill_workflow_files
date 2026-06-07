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
    objects = _normalize_list_result(faasr_get_folder_list(prefix=folder))
    files = []
    for obj in objects:
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


def _prepare_raw_df(df: pd.DataFrame, serial: str, source_path: str) -> pd.DataFrame:
    df = df.copy()
    df["logger_serial_number"] = serial
    df["source_file"] = source_path
    if "port_num" in df.columns:
        df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").astype("Int64")
    if "timestamp_utc" in df.columns:
        df["timestamp_utc"] = pd.to_numeric(df["timestamp_utc"], errors="coerce")
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


def read_zentra_raw_files(
    raw_prefix: str = "zentra_raw_backfill",
    staging_prefix: str = "zentra_phase2_staging",
    max_files_per_serial: str = "ALL",
):
    """
    Function 1:
    Read raw historical backfill CSV files from S3 and stage combined per-logger CSVs.

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
        "max_files_per_serial": max_files_per_serial,
        "serials": {},
    }

    # Save mapping for transparency and for the next step.
    mapping_file = "mapping_used.csv"
    mapping.to_csv(mapping_file, index=False)
    faasr_put_file(local_file=mapping_file, remote_folder=staging_prefix, remote_file=mapping_file)

    for serial in serials:
        raw_folder = f"{raw_prefix}/{serial}"
        raw_files = _list_csv_files(raw_folder, max_files=max_files_per_serial)
        pieces = []

        for i, (remote_folder, remote_file, source_path) in enumerate(raw_files):
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
            "staged_rows": int(len(combined)),
            "staged_path": f"{staging_prefix}/raw_by_serial/{staged_file}",
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
            if "port_num" in df_serial.columns:
                df_serial["port_num"] = pd.to_numeric(df_serial["port_num"], errors="coerce").astype("Int64")
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
    Upload/copy generated 12 configuration CSVs into the final S3 output folder.

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
        df = _download_csv(generated_prefix, filename, f"final_{filename}")
        df.to_csv(filename, index=False)
        faasr_put_file(local_file=filename, remote_folder=output_prefix, remote_file=filename)
        upload_summary["uploaded_files"].append({
            "file": filename,
            "rows": int(len(df)),
            "remote_path": f"{output_prefix}/{filename}",
        })
        faasr_log(f"Uploaded final {output_prefix}/{filename} with {len(df)} rows")

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
