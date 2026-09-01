import json
from pathlib import Path
import re
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from FaaSr_py.client.py_client_stubs import (
    faasr_get_file,
    faasr_get_folder_list,
    faasr_log,
    faasr_put_file,
    faasr_secret,
)

# ---------------------------------------------------------------------
# SMART-TAP / ORCHARDGRASS LOGGER + PORT INVENTORY
# ---------------------------------------------------------------------

DEFAULT_SERIAL_NUMBERS = [
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

LOGGER_PORTS = {
    "z6-19600": [2, 4, 5],
    "z6-12196": [3, 5, 6],
    "z6-19602": [2],
    "z6-19604": [3],
    "z6-19597": [2, 3],
    "z6-19594": [2, 3, 4, 5, 6],
    "z6-19599": [3],
    "z6-12197": [2, 3, 4],
    "z6-19595": [2, 3, 4, 5, 6],
    "z6-19598": [3, 4, 5],
    "z6-12202": [2, 3, 4],
    "z6-19596": [2, 3, 4, 5, 6],
    "z6-19603": [2, 3, 4, 5],
}

# Informational only. Internal-gap detection already uses first/last observed
# dates, so it will never invent gaps before a port actually appears.
PORT_EFFECTIVE_START_LOCAL = {
    ("z6-19598", 5): "2025-08-21",
}

PLAN_COLUMNS = [
    "gap_id",
    "logger_serial_number",
    "port_num",
    "missing_local_date",
    "previous_present_date",
    "next_present_date",
    "first_present_date",
    "last_present_date",
    "consecutive_gap_length",
    "gap_run_start",
    "gap_run_end",
]

# ---------------------------------------------------------------------
# BASIC HELPERS
# ---------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _serials(value: Any) -> list[str]:
    if value is None or str(value).strip().upper() == "ALL":
        return list(DEFAULT_SERIAL_NUMBERS)
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if s.startswith("["):
        parsed = json.loads(s)
        return [str(x).strip() for x in parsed if str(x).strip()]
    return [x.strip() for x in s.split(",") if x.strip()]


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _exists(remote_folder: str, remote_file: str) -> bool:
    remote_folder = remote_folder.strip().rstrip("/")
    key = (
        f"{remote_folder}/{remote_file}"
        if remote_folder
        else remote_file
    )

    try:
        objects = _normalize_list_result(
            faasr_get_folder_list(prefix=key)
        )
    except Exception:
        return False

    for obj in objects:
        s = str(obj).strip().lstrip("/")
        if (
            s == key
            or s.endswith(f"/{remote_file}")
            or s == remote_file
        ):
            return True
    return False


def _remote_folder_and_file(
    default_folder: str,
    source_path: str,
) -> tuple[str, str]:
    source_path = str(source_path).strip().lstrip("/")
    if "/" in source_path:
        return (
            "/".join(source_path.split("/")[:-1]),
            source_path.split("/")[-1],
        )
    return default_folder.strip().rstrip("/"), source_path


def _put_json(
    obj: dict,
    remote_folder: str,
    remote_file: str,
) -> None:
    with open(remote_file, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)

    faasr_put_file(
        local_file=remote_file,
        remote_folder=remote_folder,
        remote_file=remote_file,
    )


def _load_json(
    remote_folder: str,
    remote_file: str,
) -> dict:
    if not _exists(remote_folder, remote_file):
        return {}

    local = f"_download_{remote_file}".replace("/", "_")
    faasr_get_file(
        local_file=local,
        remote_folder=remote_folder,
        remote_file=remote_file,
    )
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f)


def _auth(token: str) -> dict[str, str]:
    token = str(token).strip()
    if not token.lower().startswith("token "):
        token = f"Token {token}"
    return {"Authorization": token}


def _zentra_dt(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S")
    )


def _stamp(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .strftime("%Y%m%dT%H%M%SZ")
    )


def _safe_int(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(value)
    except Exception:
        return None


# ---------------------------------------------------------------------
# GAP AUDIT
# ---------------------------------------------------------------------


def _empty_audit_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["datetime", "timestamp_utc", "port_num"]
    )


def _read_minimal_audit_csv(
    local_path: str,
) -> tuple[pd.DataFrame, str]:
    """
    Read only date/port columns from a CSV.

    Returns:
      dataframe, status

    status:
      ok
      empty_file
      no_relevant_columns
      unreadable
    """
    try:
        if not Path(local_path).exists():
            return _empty_audit_frame(), "unreadable"

        if Path(local_path).stat().st_size == 0:
            return _empty_audit_frame(), "empty_file"

        df = pd.read_csv(
            local_path,
            usecols=lambda c: c in {
                "datetime",
                "timestamp_utc",
                "port_num",
            },
        )

        if df.empty and len(df.columns) == 0:
            return _empty_audit_frame(), "empty_file"

        if "port_num" not in df.columns:
            return _empty_audit_frame(), "no_relevant_columns"

        if (
            "datetime" not in df.columns
            and "timestamp_utc" not in df.columns
        ):
            return _empty_audit_frame(), "no_relevant_columns"

        # Ensure all three audit columns exist so later concatenation is stable.
        for col in ["datetime", "timestamp_utc", "port_num"]:
            if col not in df.columns:
                df[col] = pd.NA

        return (
            df[["datetime", "timestamp_utc", "port_num"]].copy(),
            "ok",
        )

    except pd.errors.EmptyDataError:
        return _empty_audit_frame(), "empty_file"
    except Exception as exc:
        faasr_log(
            f"Could not parse audit CSV {local_path}: {exc}"
        )
        return _empty_audit_frame(), "unreadable"


def _list_raw_csv_paths_for_serial(
    raw_prefix: str,
    serial: str,
) -> list[str]:
    """
    List raw CSV objects for one logger directly from the raw S3 source folder.
    Used only as a fallback when raw_by_serial is empty/missing/unreadable.
    """
    folder = f"{raw_prefix.strip().rstrip('/')}/{serial}"

    try:
        objects = _normalize_list_result(
            faasr_get_folder_list(prefix=folder)
        )
    except Exception as exc:
        faasr_log(
            f"Could not list fallback raw folder {folder}: {exc}"
        )
        return []

    paths = []
    for obj in objects:
        s = str(obj).strip().lstrip("/")
        if not s.lower().endswith(".csv"):
            continue

        # FaaSr may return full S3 key or a bare filename.
        if "/" not in s:
            s = f"{folder}/{s}"

        if s.startswith(folder + "/"):
            paths.append(s)

    return sorted(set(paths))


def _load_raw_s3_fallback_for_audit(
    raw_prefix: str,
    serial: str,
) -> tuple[pd.DataFrame, dict]:
    """
    Slow but authoritative fallback.

    If the combined raw_by_serial file is empty or unusable, inspect this
    logger's original raw S3 CSVs so one bad debugging file does not abort or
    invalidate the historical gap audit.
    """
    paths = _list_raw_csv_paths_for_serial(
        raw_prefix=raw_prefix,
        serial=serial,
    )

    pieces = []
    empty_files = 0
    unreadable_files = 0
    no_relevant_columns = 0

    for i, source_path in enumerate(paths):
        remote_folder, remote_file = _remote_folder_and_file(
            "",
            source_path,
        )
        local = f"_gap_fallback_{serial}_{i}.csv"

        try:
            faasr_get_file(
                local_file=local,
                remote_folder=remote_folder,
                remote_file=remote_file,
            )
        except Exception as exc:
            unreadable_files += 1
            faasr_log(
                f"Fallback audit could not download "
                f"{source_path}: {exc}"
            )
            continue

        df, status = _read_minimal_audit_csv(local)

        if status == "ok":
            if not df.empty:
                pieces.append(df)
        elif status == "empty_file":
            empty_files += 1
        elif status == "no_relevant_columns":
            no_relevant_columns += 1
        else:
            unreadable_files += 1

        if (i + 1) % 100 == 0:
            faasr_log(
                f"{serial}: fallback audited "
                f"{i + 1}/{len(paths)} raw files"
            )

    combined = (
        pd.concat(pieces, ignore_index=True)
        if pieces
        else _empty_audit_frame()
    )

    summary = {
        "fallback_used": True,
        "raw_files_listed": int(len(paths)),
        "raw_files_with_usable_rows": int(len(pieces)),
        "empty_raw_files": int(empty_files),
        "unreadable_raw_files": int(unreadable_files),
        "raw_files_without_required_audit_columns": int(
            no_relevant_columns
        ),
        "fallback_rows_loaded": int(len(combined)),
    }

    return combined, summary


def _load_raw_by_serial_for_audit(
    raw_by_serial_prefix: str,
    raw_prefix: str,
    serial: str,
    fallback_to_raw_s3: Any = True,
) -> tuple[pd.DataFrame, dict]:
    """
    Preferred audit source:
      raw_by_serial/<serial>_raw_combined.csv

    Robust behavior:
      - normal combined CSV -> use it directly
      - zero-byte / headerless / unreadable / missing combined CSV ->
        optionally fall back to the original raw S3 files for that logger
      - if both sources contain no usable rows, return an empty audit frame
        and REPORT the condition instead of crashing the workflow
    """
    filename = f"{serial}_raw_combined.csv"
    fallback_bool = _truthy(fallback_to_raw_s3)

    source_summary = {
        "logger_serial_number": serial,
        "raw_by_serial_path": (
            f"{raw_by_serial_prefix.rstrip('/')}/{filename}"
        ),
        "raw_by_serial_status": None,
        "fallback_used": False,
    }

    if _exists(raw_by_serial_prefix, filename):
        local = f"_gap_audit_{serial}.csv"

        try:
            faasr_get_file(
                local_file=local,
                remote_folder=raw_by_serial_prefix,
                remote_file=filename,
            )
            df, read_status = _read_minimal_audit_csv(local)
        except Exception as exc:
            df = _empty_audit_frame()
            read_status = "download_failed"
            source_summary["raw_by_serial_error"] = str(exc)

        source_summary["raw_by_serial_status"] = read_status
        source_summary["raw_by_serial_rows_loaded"] = int(
            len(df)
        )

        if read_status == "ok" and not df.empty:
            source_summary["audit_source"] = "raw_by_serial"
            return df, source_summary

        faasr_log(
            f"WARNING: {serial} raw_by_serial file is "
            f"{read_status} / rows={len(df)}."
        )

    else:
        source_summary["raw_by_serial_status"] = "missing"
        faasr_log(
            f"WARNING: {serial} raw_by_serial file is missing."
        )

    if fallback_bool:
        faasr_log(
            f"{serial}: falling back to original raw S3 files "
            "for the gap audit."
        )
        fallback_df, fallback_summary = (
            _load_raw_s3_fallback_for_audit(
                raw_prefix=raw_prefix,
                serial=serial,
            )
        )
        source_summary.update(fallback_summary)

        if not fallback_df.empty:
            source_summary["audit_source"] = "raw_s3_fallback"
            return fallback_df, source_summary

    source_summary["audit_source"] = "no_usable_source_rows"
    return _empty_audit_frame(), source_summary


def _local_dates_from_df(
    df: pd.DataFrame,
    timezone_name: str,
) -> pd.Series:
    """
    Convert raw observations to local calendar dates.

    Prefer the timezone-aware `datetime` field. Fall back to epoch-seconds
    `timestamp_utc` when needed.
    """
    timezone_name = str(timezone_name).strip()

    if "datetime" in df.columns:
        dt = pd.to_datetime(
            df["datetime"],
            errors="coerce",
            utc=True,
        )
    else:
        dt = pd.Series(pd.NaT, index=df.index)

    if "timestamp_utc" in df.columns:
        missing = dt.isna()
        if missing.any():
            epoch = pd.to_numeric(
                df.loc[missing, "timestamp_utc"],
                errors="coerce",
            )
            dt.loc[missing] = pd.to_datetime(
                epoch,
                unit="s",
                errors="coerce",
                utc=True,
            )

    return dt.dt.tz_convert(timezone_name).dt.date


def _missing_runs(
    present_dates: list[date],
) -> list[dict[str, Any]]:
    """
    Return consecutive internal missing-date runs.

    By construction only dates strictly between first and last present date
    can be flagged. Leading/trailing missing dates are intentionally ignored,
    matching the user's definition of a gap.
    """
    if len(present_dates) < 2:
        return []

    present_dates = sorted(set(present_dates))
    first_date = present_dates[0]
    last_date = present_dates[-1]
    present_set = set(present_dates)

    all_dates = pd.date_range(
        first_date,
        last_date,
        freq="D",
    ).date

    missing = [d for d in all_dates if d not in present_set]
    if not missing:
        return []

    runs = []
    run_start = missing[0]
    run_dates = [missing[0]]

    for d in missing[1:]:
        if d == run_dates[-1] + timedelta(days=1):
            run_dates.append(d)
        else:
            runs.append({
                "run_start": run_start,
                "run_end": run_dates[-1],
                "dates": run_dates,
            })
            run_start = d
            run_dates = [d]

    runs.append({
        "run_start": run_start,
        "run_end": run_dates[-1],
        "dates": run_dates,
    })

    return runs


def _audit_one_port(
    df: pd.DataFrame,
    serial: str,
    port: int,
    timezone_name: str,
) -> tuple[list[dict], dict]:
    work = df.copy()
    work["port_num"] = pd.to_numeric(
        work["port_num"],
        errors="coerce",
    )
    work = work[work["port_num"] == int(port)].copy()

    if work.empty:
        # No "before and after", so by the user's definition this is not an
        # internal gap. Report it separately as no observed history.
        return [], {
            "logger_serial_number": serial,
            "port_num": int(port),
            "status": "no_observations_for_configured_port",
            "first_present_date": None,
            "last_present_date": None,
            "present_day_count": 0,
            "internal_missing_day_count": 0,
        }

    work["_local_date"] = _local_dates_from_df(
        work,
        timezone_name=timezone_name,
    )
    work = work.dropna(subset=["_local_date"])

    present_dates = sorted(
        set(work["_local_date"].tolist())
    )

    if not present_dates:
        return [], {
            "logger_serial_number": serial,
            "port_num": int(port),
            "status": "no_parseable_dates",
            "first_present_date": None,
            "last_present_date": None,
            "present_day_count": 0,
            "internal_missing_day_count": 0,
        }

    first_date = present_dates[0]
    last_date = present_dates[-1]

    rows = []
    for run in _missing_runs(present_dates):
        run_dates = run["dates"]

        previous_present = max(
            d for d in present_dates
            if d < run["run_start"]
        )
        next_present = min(
            d for d in present_dates
            if d > run["run_end"]
        )

        for missing_date in run_dates:
            gap_id = (
                f"{serial}|port-{int(port)}|"
                f"{missing_date.isoformat()}"
            )
            rows.append({
                "gap_id": gap_id,
                "logger_serial_number": serial,
                "port_num": int(port),
                "missing_local_date": missing_date.isoformat(),
                "previous_present_date": previous_present.isoformat(),
                "next_present_date": next_present.isoformat(),
                "first_present_date": first_date.isoformat(),
                "last_present_date": last_date.isoformat(),
                "consecutive_gap_length": len(run_dates),
                "gap_run_start": run["run_start"].isoformat(),
                "gap_run_end": run["run_end"].isoformat(),
            })

    summary = {
        "logger_serial_number": serial,
        "port_num": int(port),
        "status": "ok",
        "effective_start_note": PORT_EFFECTIVE_START_LOCAL.get(
            (serial, int(port))
        ),
        "first_present_date": first_date.isoformat(),
        "last_present_date": last_date.isoformat(),
        "present_day_count": int(len(present_dates)),
        "internal_missing_day_count": int(len(rows)),
    }
    return rows, summary


def audit_internal_date_gaps(
    raw_by_serial_prefix: str = (
        "zentra_phase2_staging_manifest_v2_full_parallel/"
        "raw_by_serial"
    ),
    raw_prefix: str = "zentra_raw_backfill",
    audit_prefix: str = "zentra_gap_audit",
    timezone_name: str = "America/Los_Angeles",
    serial_numbers: Any = "ALL",
    fail_on_no_observations: Any = False,
    fallback_to_raw_s3: Any = True,
):
    """
    Find FULL local calendar dates that are missing for a logger/port while
    data exist before AND after the gap.

    This intentionally detects only complete missing dates. It does not flag:
      - partial-day gaps,
      - missing readings within a day,
      - leading dates before the first observation,
      - trailing dates after the last observation.

    Audit source:
      raw_by_serial/<serial>_raw_combined.csv

    This keeps the audit fast: 13 combined logger files are inspected instead
    of re-downloading thousands of historical raw S3 objects.
    """
    serial_list = _serials(serial_numbers)
    run_timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    fail_on_no_observations_bool = _truthy(
        fail_on_no_observations
    )

    all_gap_rows = []
    port_summaries = []
    serial_summaries = {}
    no_observation_ports = []
    audit_source_summaries = []

    for serial in serial_list:
        if serial not in LOGGER_PORTS:
            raise ValueError(
                f"No configured port mapping for {serial}."
            )

        faasr_log(
            f"Auditing internal missing dates for {serial}."
        )
        df, source_summary = _load_raw_by_serial_for_audit(
            raw_by_serial_prefix=raw_by_serial_prefix,
            raw_prefix=raw_prefix,
            serial=serial,
            fallback_to_raw_s3=fallback_to_raw_s3,
        )
        audit_source_summaries.append(source_summary)

        faasr_log(
            f"{serial}: audit_source="
            f"{source_summary.get('audit_source')}; "
            f"rows={len(df)}"
        )

        serial_gap_count = 0
        serial_port_summaries = []

        for port in LOGGER_PORTS[serial]:
            gap_rows, port_summary = _audit_one_port(
                df=df,
                serial=serial,
                port=int(port),
                timezone_name=timezone_name,
            )
            all_gap_rows.extend(gap_rows)
            port_summaries.append(port_summary)
            serial_port_summaries.append(port_summary)
            serial_gap_count += len(gap_rows)

            if (
                port_summary.get("status")
                == "no_observations_for_configured_port"
            ):
                no_observation_ports.append(port_summary)
                faasr_log(
                    f"WARNING: {serial} port {port} has no observations "
                    "at all in raw_by_serial. This is NOT an internal-date "
                    "gap because there is no 'before' and 'after'. "
                    "It will be reported and skipped."
                )
            else:
                faasr_log(
                    f"{serial} port {port}: "
                    f"internal_missing_days={len(gap_rows)}"
                )

        serial_summaries[serial] = {
            "audit_source": source_summary,
            "ports": serial_port_summaries,
            "internal_missing_day_count": int(serial_gap_count),
        }

    audit_source_df = pd.DataFrame(audit_source_summaries)
    audit_source_file = "audit_source_status_latest.csv"
    audit_source_df.to_csv(audit_source_file, index=False)
    faasr_put_file(
        local_file=audit_source_file,
        remote_folder=audit_prefix,
        remote_file=audit_source_file,
    )

    # A configured port with zero observations is NOT an internal gap under
    # the user's definition. Report it separately and continue by default.
    no_obs_df = pd.DataFrame(no_observation_ports)
    no_obs_file = "no_observation_ports_latest.csv"
    no_obs_df.to_csv(no_obs_file, index=False)
    faasr_put_file(
        local_file=no_obs_file,
        remote_folder=audit_prefix,
        remote_file=no_obs_file,
    )

    if no_observation_ports and fail_on_no_observations_bool:
        raise RuntimeError(
            "Gap audit found configured logger/port combinations with no "
            "observations at all. Strict mode was requested. "
            f"Details: {no_observation_ports}"
        )

    gap_df = pd.DataFrame(
        all_gap_rows,
        columns=PLAN_COLUMNS,
    )

    plan_csv = "internal_gap_plan_latest.csv"
    gap_df.to_csv(plan_csv, index=False)
    faasr_put_file(
        local_file=plan_csv,
        remote_folder=audit_prefix,
        remote_file=plan_csv,
    )

    timestamped_csv = (
        f"internal_gap_plan_{run_timestamp}.csv"
    )
    gap_df.to_csv(timestamped_csv, index=False)
    faasr_put_file(
        local_file=timestamped_csv,
        remote_folder=audit_prefix,
        remote_file=timestamped_csv,
    )

    plan = {
        "created_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "timezone_name": timezone_name,
        "definition": (
            "A gap is a full local calendar date with zero rows for a "
            "configured logger+port, where at least one observed date exists "
            "before the gap and at least one observed date exists after it."
        ),
        "scope_note": (
            "Only complete missing local dates are checked. Partial-day "
            "missingness is outside this audit."
        ),
        "raw_by_serial_prefix": raw_by_serial_prefix,
        "raw_prefix": raw_prefix,
        "fallback_to_raw_s3": _truthy(fallback_to_raw_s3),
        "audit_prefix": audit_prefix,
        "audit_source_summaries": audit_source_summaries,
        "audit_source_report_csv": (
            f"{audit_prefix}/{audit_source_file}"
        ),
        "serial_numbers": serial_list,
        "total_internal_missing_days": int(len(gap_df)),
        "gaps": all_gap_rows,
        "serials": serial_summaries,
        "ports": port_summaries,
        "no_observation_ports": no_observation_ports,
        "no_observation_port_count": int(len(no_observation_ports)),
        "no_observation_report_csv": (
            f"{audit_prefix}/{no_obs_file}"
        ),
        "fail_on_no_observations": fail_on_no_observations_bool,
        "plan_csv": f"{audit_prefix}/{plan_csv}",
        "status": (
            "gaps_found_with_no_observation_warnings"
            if len(gap_df) > 0 and no_observation_ports
            else "gaps_found"
            if len(gap_df) > 0
            else "no_internal_gaps_but_no_observation_warnings"
            if no_observation_ports
            else "no_internal_date_gaps"
        ),
    }

    _put_json(
        plan,
        audit_prefix,
        "internal_gap_plan_latest.json",
    )
    _put_json(
        plan,
        audit_prefix,
        f"internal_gap_plan_{run_timestamp}.json",
    )

    faasr_log(
        "Internal-date-gap audit complete. "
        f"total_missing_days={len(gap_df)}, "
        f"no_observation_warnings={len(no_observation_ports)}. "
        "No-observation configured ports were skipped because they do not "
        "satisfy the internal-gap definition."
    )


# ---------------------------------------------------------------------
# ZENTRA API GAP REPAIR
# ---------------------------------------------------------------------


def _fetch_page(
    token: str,
    serial: str,
    start_s: str,
    end_s: str,
    page_num: int,
    per_page: int,
    api_version: str,
    server: str,
) -> pd.DataFrame:
    url = f"{server}/api/{api_version}/get_readings/"
    params = {
        "output_format": "df",
        "per_page": int(per_page),
        "page_num": int(page_num),
        "sort_by": "ascending",
        "start_date": start_s,
        "end_date": end_s,
        "device_sn": serial,
    }

    response = requests.get(
        url,
        headers=_auth(token),
        params=params,
        timeout=60,
    )

    if response.status_code == 429:
        raise RuntimeError("RATE_LIMIT_429")

    response.raise_for_status()
    payload = response.json()
    data = payload["data"]

    if isinstance(data, str):
        data = json.loads(data)

    try:
        return pd.DataFrame(**data)
    except TypeError:
        return pd.DataFrame(data)


def _fetch_page_retry(
    token: str,
    serial: str,
    start_s: str,
    end_s: str,
    page_num: int,
    per_page: int,
    api_version: str,
    server: str,
    sleep_seconds: int,
) -> pd.DataFrame:
    for attempt in range(1, 6):
        try:
            return _fetch_page(
                token=token,
                serial=serial,
                start_s=start_s,
                end_s=end_s,
                page_num=page_num,
                per_page=per_page,
                api_version=api_version,
                server=server,
            )

        except RuntimeError as exc:
            if "RATE_LIMIT_429" in str(exc):
                faasr_log(
                    f"429 rate limit for {serial}; "
                    f"sleeping {sleep_seconds}s."
                )
                time.sleep(int(sleep_seconds))
                continue
            raise

        except requests.RequestException as exc:
            if attempt == 5:
                raise

            wait = min(30 * attempt, 120)
            faasr_log(
                f"Request failed for {serial}, "
                f"attempt {attempt}/5: {exc}. "
                f"Sleeping {wait}s."
            )
            time.sleep(wait)

    raise RuntimeError(
        f"Unable to fetch page {page_num} for {serial}."
    )


def _target_day_utc_window(
    local_date_text: str,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    """
    Convert a local calendar day to exact UTC bounds.

    Construct the next local midnight separately so DST transitions are handled
    correctly (a local day can be 23, 24, or 25 hours).
    """
    local_zone = ZoneInfo(timezone_name)
    d = date.fromisoformat(local_date_text)
    next_d = d + timedelta(days=1)

    start_local = datetime.combine(
        d,
        dt_time.min,
        tzinfo=local_zone,
    )
    end_local = datetime.combine(
        next_d,
        dt_time.min,
        tzinfo=local_zone,
    )

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _filter_target_port_day(
    df: pd.DataFrame,
    port: int,
    local_date_text: str,
    timezone_name: str,
) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    if "port_num" not in df.columns:
        raise ValueError(
            "Zentra API response does not contain port_num."
        )

    out = df.copy()
    out["port_num"] = pd.to_numeric(
        out["port_num"],
        errors="coerce",
    )
    out = out[out["port_num"] == int(port)].copy()

    if out.empty:
        return out

    if "datetime" in out.columns:
        dt = pd.to_datetime(
            out["datetime"],
            errors="coerce",
            utc=True,
        )
    elif "timestamp_utc" in out.columns:
        epoch = pd.to_numeric(
            out["timestamp_utc"],
            errors="coerce",
        )
        dt = pd.to_datetime(
            epoch,
            unit="s",
            errors="coerce",
            utc=True,
        )
    else:
        raise ValueError(
            "Zentra API response has neither datetime "
            "nor timestamp_utc."
        )

    local_date = (
        dt.dt.tz_convert(timezone_name)
        .dt.strftime("%Y-%m-%d")
    )

    out = out[
        local_date == str(local_date_text)
    ].copy()

    return out.reset_index(drop=True)


def _repair_filename(
    serial: str,
    port: int,
    local_date_text: str,
    start_utc: datetime,
    end_utc: datetime,
) -> str:
    local_compact = str(local_date_text).replace("-", "")
    return (
        f"gaprepair_{serial}_port-{int(port)}_"
        f"localdate-{local_compact}_"
        f"{_stamp(start_utc)}_to_{_stamp(end_utc)}.csv"
    )


def _fetch_one_gap_day(
    token: str,
    serial: str,
    port: int,
    local_date_text: str,
    timezone_name: str,
    per_page: int,
    sleep_seconds: int,
    server: str,
    api_version: str,
    api_calls_already_used: int,
    max_api_calls_per_run: int,
) -> tuple[pd.DataFrame, int, datetime, datetime]:
    start_utc, end_utc = _target_day_utc_window(
        local_date_text=local_date_text,
        timezone_name=timezone_name,
    )

    start_s = _zentra_dt(start_utc)
    end_s = _zentra_dt(end_utc)

    pages = []
    page_num = 1
    calls_used = int(api_calls_already_used)

    while True:
        if calls_used >= int(max_api_calls_per_run):
            raise RuntimeError(
                "MAX_API_CALLS_REACHED"
            )

        if calls_used > int(api_calls_already_used):
            time.sleep(int(sleep_seconds))

        faasr_log(
            f"Gap repair fetch: {serial} port {port}, "
            f"local_date={local_date_text}, "
            f"UTC={start_s} to {end_s}, page={page_num}"
        )

        raw = _fetch_page_retry(
            token=token,
            serial=serial,
            start_s=start_s,
            end_s=end_s,
            page_num=page_num,
            per_page=int(per_page),
            api_version=api_version,
            server=server,
            sleep_seconds=int(sleep_seconds),
        )
        calls_used += 1

        filtered = _filter_target_port_day(
            df=raw,
            port=int(port),
            local_date_text=local_date_text,
            timezone_name=timezone_name,
        )

        if not filtered.empty:
            pages.append(filtered)

        # Pagination termination must use the raw logger response length,
        # not the filtered port length.
        if len(raw) < int(per_page):
            break

        page_num += 1

    result = (
        pd.concat(pages, ignore_index=True)
        if pages
        else pd.DataFrame()
    )

    return result, calls_used, start_utc, end_utc


def repair_internal_date_gaps(
    audit_prefix: str = "zentra_gap_audit",
    plan_file: str = "internal_gap_plan_latest.json",
    progress_file: str = "internal_gap_repair_progress.json",
    raw_prefix: str = "zentra_raw_backfill",
    timezone_name: str = "America/Los_Angeles",
    per_page: int = 2000,
    sleep_seconds: int = 65,
    server: str = "https://zentracloud.com",
    api_version: str = "v4",
    max_gap_days_per_run: int = 50,
    max_api_calls_per_run: int = 100,
    retry_unresolved: Any = False,
):
    """
    Repair each planned missing local date.

    Important:
    - Zentra's public get_readings API is queried by logger serial number.
      It does not expose a reliable documented port parameter here, so the
      response is filtered to the requested port immediately after download.
    - The uploaded repair CSV contains ONLY that target port and local date.
    - API empty result is recorded as unresolved; no synthetic data are made.
    - The workflow is resumable through internal_gap_repair_progress.json.
    """
    retry_unresolved_bool = _truthy(retry_unresolved)
    token = faasr_secret("ZENTRA_TOKEN")

    plan = _load_json(audit_prefix, plan_file)
    if not plan:
        raise RuntimeError(
            f"Missing gap plan: {audit_prefix}/{plan_file}"
        )

    gaps = plan.get("gaps", [])
    if not isinstance(gaps, list):
        raise ValueError(
            "Gap plan has invalid 'gaps' structure."
        )

    progress = _load_json(
        audit_prefix,
        progress_file,
    )
    if not progress:
        progress = {
            "created_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "plan_created_at_utc": plan.get(
                "created_at_utc"
            ),
            "plan_file": f"{audit_prefix}/{plan_file}",
            "timezone_name": timezone_name,
            "gaps": {},
        }

    progress.setdefault("gaps", {})

    if not gaps:
        summary = {
            "created_at_utc": datetime.now(
                timezone.utc
            ).isoformat(),
            "status": "nothing_to_repair",
            "total_planned_gap_days": 0,
            "repaired_or_already_present": 0,
            "unresolved_no_api_data": 0,
            "failed": 0,
            "remaining_unprocessed": 0,
            "repair_files": [],
        }
        _put_json(
            summary,
            audit_prefix,
            "internal_gap_repair_summary_latest.json",
        )
        faasr_log(
            "Gap repair: audit found no internal missing dates."
        )
        return

    calls_used = 0
    gap_days_attempted = 0
    repair_files = []

    for gap in gaps:
        gap_id = str(gap["gap_id"])
        serial = str(gap["logger_serial_number"])
        port = int(gap["port_num"])
        missing_date = str(gap["missing_local_date"])

        existing_state = progress["gaps"].get(
            gap_id,
            {},
        )
        prior_status = existing_state.get("status")

        if prior_status in {
            "repaired",
            "repair_file_already_exists",
        }:
            continue

        if (
            prior_status == "unresolved_no_api_data"
            and not retry_unresolved_bool
        ):
            continue

        if gap_days_attempted >= int(max_gap_days_per_run):
            break

        if calls_used >= int(max_api_calls_per_run):
            break

        start_utc, end_utc = _target_day_utc_window(
            missing_date,
            timezone_name,
        )
        filename = _repair_filename(
            serial=serial,
            port=port,
            local_date_text=missing_date,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        folder = f"{raw_prefix.rstrip('/')}/{serial}"

        # Crash-safe idempotency: if the deterministic repair file was already
        # uploaded but progress was not committed, do not hit the API again.
        if _exists(folder, filename):
            progress["gaps"][gap_id] = {
                **gap,
                "status": "repair_file_already_exists",
                "repair_path": f"{folder}/{filename}",
                "updated_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
            repair_files.append(
                f"{folder}/{filename}"
            )
            _put_json(
                progress,
                audit_prefix,
                progress_file,
            )
            continue

        gap_days_attempted += 1

        try:
            repaired_df, new_calls_used, start_utc, end_utc = (
                _fetch_one_gap_day(
                    token=token,
                    serial=serial,
                    port=port,
                    local_date_text=missing_date,
                    timezone_name=timezone_name,
                    per_page=int(per_page),
                    sleep_seconds=int(sleep_seconds),
                    server=server,
                    api_version=api_version,
                    api_calls_already_used=calls_used,
                    max_api_calls_per_run=int(
                        max_api_calls_per_run
                    ),
                )
            )
            calls_used = new_calls_used

            if repaired_df.empty:
                progress["gaps"][gap_id] = {
                    **gap,
                    "status": "unresolved_no_api_data",
                    "rows_recovered": 0,
                    "utc_start": start_utc.isoformat(),
                    "utc_end": end_utc.isoformat(),
                    "updated_at_utc": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                faasr_log(
                    f"No API rows recovered for {gap_id}. "
                    "Leaving gap unresolved."
                )

            else:
                # Final safety check before upload.
                if "port_num" not in repaired_df.columns:
                    raise RuntimeError(
                        f"{gap_id}: repaired dataframe "
                        "has no port_num."
                    )

                unique_ports = sorted(
                    pd.to_numeric(
                        repaired_df["port_num"],
                        errors="coerce",
                    )
                    .dropna()
                    .astype(int)
                    .unique()
                    .tolist()
                )

                if unique_ports != [port]:
                    raise RuntimeError(
                        f"{gap_id}: repair contains unexpected "
                        f"ports {unique_ports}."
                    )

                repaired_df.to_csv(
                    filename,
                    index=False,
                )
                faasr_put_file(
                    local_file=filename,
                    remote_folder=folder,
                    remote_file=filename,
                )

                repair_path = (
                    f"{folder}/{filename}"
                )
                repair_files.append(repair_path)

                progress["gaps"][gap_id] = {
                    **gap,
                    "status": "repaired",
                    "rows_recovered": int(
                        len(repaired_df)
                    ),
                    "repair_path": repair_path,
                    "utc_start": start_utc.isoformat(),
                    "utc_end": end_utc.isoformat(),
                    "updated_at_utc": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }

                faasr_log(
                    f"Repaired {gap_id}: "
                    f"rows={len(repaired_df)}, "
                    f"path={repair_path}"
                )

            # Persist after every gap so the job is resumable.
            progress["updated_at_utc"] = datetime.now(
                timezone.utc
            ).isoformat()
            progress["api_calls_used_last_run"] = int(
                calls_used
            )
            _put_json(
                progress,
                audit_prefix,
                progress_file,
            )

        except RuntimeError as exc:
            if "MAX_API_CALLS_REACHED" in str(exc):
                faasr_log(
                    "Maximum API calls reached; "
                    "saving progress and stopping cleanly."
                )
                break

            progress["gaps"][gap_id] = {
                **gap,
                "status": "failed",
                "error": str(exc),
                "updated_at_utc": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
            _put_json(
                progress,
                audit_prefix,
                progress_file,
            )
            raise

        # Conservative spacing between target-day API requests.
        if (
            int(sleep_seconds) > 0
            and calls_used < int(max_api_calls_per_run)
        ):
            time.sleep(int(sleep_seconds))

    states = progress.get("gaps", {})

    repaired_count = sum(
        1
        for x in states.values()
        if x.get("status") in {
            "repaired",
            "repair_file_already_exists",
        }
    )
    unresolved_count = sum(
        1
        for x in states.values()
        if x.get("status") == "unresolved_no_api_data"
    )
    failed_count = sum(
        1
        for x in states.values()
        if x.get("status") == "failed"
    )

    total_planned = len(gaps)

    processed_ids = set(states)
    remaining_unprocessed = sum(
        1
        for gap in gaps
        if str(gap["gap_id"]) not in processed_ids
    )

    if failed_count:
        status = "failed"
    elif remaining_unprocessed:
        status = "partial_more_runs_needed"
    elif unresolved_count:
        status = "complete_with_unresolved_source_gaps"
    else:
        status = "complete_all_repaired"

    all_repair_paths = sorted(
        set(
            x.get("repair_path")
            for x in states.values()
            if x.get("repair_path")
        )
    )

    # A summary shaped with serials.remote_paths is useful if this repair output
    # is later consumed by an exact-path downstream updater.
    by_serial: dict[str, dict] = {}
    for path in all_repair_paths:
        m = re.search(r"z6-\d+", path)
        if not m:
            continue
        serial = m.group(0)
        by_serial.setdefault(
            serial,
            {
                "status": "success",
                "remote_paths": [],
            },
        )
        by_serial[serial]["remote_paths"].append(path)

    summary = {
        "created_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": status,
        "total_planned_gap_days": int(total_planned),
        "repaired_or_already_present": int(
            repaired_count
        ),
        "unresolved_no_api_data": int(
            unresolved_count
        ),
        "failed": int(failed_count),
        "remaining_unprocessed": int(
            remaining_unprocessed
        ),
        "api_calls_used_this_run": int(
            calls_used
        ),
        "gap_days_attempted_this_run": int(
            gap_days_attempted
        ),
        "repair_files": all_repair_paths,
        "serials": by_serial,
        "progress_file": (
            f"{audit_prefix}/{progress_file}"
        ),
        "note": (
            "Unresolved_no_api_data means Zentra Cloud returned no rows "
            "for that logger/port/local date. The workflow does not "
            "manufacture or interpolate raw historical data."
        ),
    }

    _put_json(
        summary,
        audit_prefix,
        "internal_gap_repair_summary_latest.json",
    )

    timestamped_summary = (
        "internal_gap_repair_summary_"
        + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + ".json"
    )
    _put_json(
        summary,
        audit_prefix,
        timestamped_summary,
    )

    faasr_log(
        "Internal gap repair run complete. "
        f"status={status}, "
        f"repaired={repaired_count}, "
        f"unresolved={unresolved_count}, "
        f"remaining={remaining_unprocessed}"
    )


def finish_internal_gap_repair():
    """
    Terminal no-op action for a clean FaaSr DAG.
    """
    faasr_log(
        "Internal-date-gap audit/repair workflow finished."
    )
