import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from FaaSr_py.client.py_client_stubs import (
    faasr_get_file,
    faasr_get_folder_list,
    faasr_log,
    faasr_put_file,
    faasr_secret,
)

THRESHOLD_VERSION = "smarttap-thresholds-v2-anomaly-silence-weekly"

EXPECTED_LOGGERS = [
    "z6-19600", "z6-12196", "z6-19602", "z6-19604", "z6-19597",
    "z6-19594", "z6-19599", "z6-12197", "z6-19595", "z6-19598",
    "z6-12202", "z6-19596", "z6-19603",
]

CONFIG_CODES = [
    "C_O_N", "C_O_D", "C_O_F",
    "S_W_N", "S_C_N", "S_E_N",
    "S_W_D", "S_C_D", "S_E_D",
    "S_W_F", "S_C_F", "S_E_F",
]

LOGGER_STATUS_COLUMNS = [
    "logger_serial_number",
    "fetch_status",
    "rows_saved_after_port_filter",
    "latest_observation_utc",
    "age_hours_at_fetch_end",
    "logger_status",
    "reason",
]

WEEKLY_OUTPUT_COLUMNS = [
    "week_start_local",
    "week_end_local",
    "config_code",
    "Location_code",
    "Shade_zone_code",
    "Irrigation_code",
    "logger_name",
    "logger_serial_number",
    "port_num",
    "port_description",
    "measurement",
    "Weekly_Average",
    "Units",
    "days_with_data",
    "coverage_ratio",
]


THRESHOLDS = {
    "body temperature": {
        "category": "IR Camera Temperature",
        "min_value": -5.0,
        "max_value": 40.0,
        "expected_units": "C",
    },
    "target temperature": {
        "category": "IR Camera Temperature",
        "min_value": -5.0,
        "max_value": 40.0,
        "expected_units": "C",
    },
    "water content": {
        "category": "Soil Moisture",
        "min_value": 0.150,
        "max_value": 0.500,
        "expected_units": "m3/m3",
    },
    "soil temperature": {
        "category": "Soil Temperature",
        "min_value": -10.0,
        "max_value": 30.0,
        "expected_units": "C",
    },
}

ANOMALY_COLUMNS = [
    "anomaly_id",
    "alert_category",
    "logger_serial_number",
    "port_num",
    "datetime",
    "timestamp_utc",
    "measurement",
    "value",
    "units",
    "threshold_min",
    "threshold_max",
    "threshold_expected_units",
    "anomaly_reason",
    "source_file",
]


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
    m = re.search(r"z6-\d+", str(source_path))
    return m.group(0) if m else None


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


def _daily_paths_from_summary(
    state_prefix: str,
    latest_summary_file: str,
    raw_prefix: str,
) -> tuple[list[str], dict]:
    """
    Strict mode: use ONLY exact remote_path values from the current daily summary.
    No historical S3 fallback scan.
    """
    if not _exists(state_prefix, latest_summary_file):
        raise RuntimeError(
            f"Missing daily update summary: "
            f"{state_prefix}/{latest_summary_file}"
        )

    summary = _download_json(
        state_prefix,
        latest_summary_file,
        "_anomaly_daily_summary.json",
    )

    paths = []
    serials = summary.get("serials", {})
    if isinstance(serials, dict):
        for _, info in serials.items():
            if not isinstance(info, dict):
                continue
            path = info.get("remote_path")
            if path:
                path = str(path).strip().lstrip("/")
                if (
                    path.startswith(raw_prefix.rstrip("/") + "/")
                    and path.lower().endswith(".csv")
                ):
                    paths.append(path)

    return sorted(set(paths)), summary


def _load_sent_ids(alert_prefix: str, sent_ids_file: str) -> set[str]:
    if not _exists(alert_prefix, sent_ids_file):
        return set()
    data = _download_json(
        alert_prefix,
        sent_ids_file,
        "_sent_anomaly_ids.json",
    )
    return set(str(x) for x in data.get("sent_anomaly_ids", []))


def _save_sent_ids(
    alert_prefix: str,
    sent_ids_file: str,
    sent_ids: set[str],
) -> None:
    _put_json(
        {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "threshold_version": THRESHOLD_VERSION,
            "sent_anomaly_ids": sorted(sent_ids),
        },
        alert_prefix,
        sent_ids_file,
    )



def _load_logger_silence_state(
    alert_prefix: str,
    state_file: str,
) -> dict:
    if not _exists(alert_prefix, state_file):
        return {
            "active_silent_loggers": [],
            "ever_seen_loggers": [],
            "updated_at_utc": None,
        }
    data = _download_json(
        alert_prefix,
        state_file,
        "_logger_silence_state.json",
    )
    if not isinstance(data, dict):
        return {
            "active_silent_loggers": [],
            "ever_seen_loggers": [],
            "updated_at_utc": None,
        }
    return data


def _save_logger_silence_state(
    alert_prefix: str,
    state_file: str,
    active_silent_loggers: set[str],
    ever_seen_loggers: set[str],
) -> None:
    _put_json(
        {
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_silent_loggers": sorted(active_silent_loggers),
            "ever_seen_loggers": sorted(ever_seen_loggers),
        },
        alert_prefix,
        state_file,
    )


def _norm_unit(value: Any) -> str:
    s = str(value).strip().lower().replace("³", "3")
    s = s.replace(" ", "")
    aliases = {
        "c": "c",
        "°c": "c",
        "degc": "c",
        "celsius": "c",
        "m3/m3": "m3/m3",
        "m^3/m^3": "m3/m3",
        "m3m-3": "m3/m3",
        "m3m3": "m3/m3",
    }
    return aliases.get(s, s)


def _is_error_flag(value: Any) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip().lower() not in {
        "", "0", "0.0", "false", "none", "nan", "no"
    }


def _read_raw_csv(source_path: str) -> pd.DataFrame:
    folder, filename = _remote_folder_and_file("", source_path)
    local = f"_anomaly_{filename}".replace("/", "_")
    faasr_get_file(
        local_file=local,
        remote_folder=folder,
        remote_file=filename,
    )
    return pd.read_csv(local)


def _standardize_raw_df(
    df: pd.DataFrame,
    source_path: str,
) -> pd.DataFrame:
    work = df.copy()
    required = [
        "timestamp_utc",
        "datetime",
        "measurement",
        "value",
        "units",
        "port_num",
    ]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(
            f"{source_path}: missing required raw columns {missing}"
        )

    for optional in [
        "sub_sensor_index",
        "sensor_sn",
        "sensor_name",
        "error_flag",
        "error_description",
        "sensor_meta_errors",
    ]:
        if optional not in work.columns:
            work[optional] = pd.NA

    serial = _extract_serial_from_source_path(source_path)
    if not serial:
        raise ValueError(
            f"Could not derive logger serial from {source_path}"
        )

    work["logger_serial_number"] = serial
    work["source_file"] = source_path
    work["port_num"] = pd.to_numeric(
        work["port_num"], errors="coerce"
    ).astype("Int64")
    work["value"] = pd.to_numeric(
        work["value"], errors="coerce"
    )
    work["datetime"] = pd.to_datetime(
        work["datetime"], errors="coerce", utc=True
    )
    work["measurement"] = (
        work["measurement"].astype(str).str.strip()
    )
    work["units"] = work["units"].astype(str).str.strip()
    return work


def _make_anomaly_id(row: pd.Series) -> str:
    """
    Logical anomaly identity deliberately excludes source_file so the same
    overlapped reading does not trigger a second email tomorrow.
    """
    parts = [
        THRESHOLD_VERSION,
        str(row.get("logger_serial_number", "")),
        str(row.get("port_num", "")),
        str(row.get("timestamp_utc", "")),
        str(row.get("datetime", "")),
        str(row.get("measurement", "")).strip().lower(),
        str(row.get("sub_sensor_index", "")),
        str(row.get("sensor_sn", "")),
        str(row.get("value", "")),
        _norm_unit(row.get("units", "")),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _detect_in_df(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict], int]:
    anomalies = []
    data_quality = []
    sensor_error_rows = 0

    if df.empty:
        return (
            pd.DataFrame(columns=ANOMALY_COLUMNS),
            data_quality,
            sensor_error_rows,
        )

    if "error_flag" in df.columns:
        error_mask = df["error_flag"].map(_is_error_flag)
        sensor_error_rows = int(error_mask.sum())
        df = df[~error_mask].copy()

    for measurement_norm, rule in THRESHOLDS.items():
        selected = df[
            df["measurement"].astype(str).str.strip().str.lower()
            == measurement_norm
        ].copy()
        if selected.empty:
            continue

        selected = selected.dropna(
            subset=["value", "datetime", "port_num"]
        ).copy()

        expected = _norm_unit(rule["expected_units"])
        selected["_norm_unit"] = selected["units"].map(_norm_unit)

        bad_unit = selected["_norm_unit"] != expected
        if bad_unit.any():
            for _, row in selected[bad_unit].head(100).iterrows():
                data_quality.append({
                    "type": "unit_mismatch",
                    "logger_serial_number": row.get(
                        "logger_serial_number"
                    ),
                    "port_num": (
                        None if pd.isna(row.get("port_num"))
                        else int(row.get("port_num"))
                    ),
                    "measurement": row.get("measurement"),
                    "units": row.get("units"),
                    "expected_units": rule["expected_units"],
                    "source_file": row.get("source_file"),
                })

        selected = selected[~bad_unit].copy()
        if selected.empty:
            continue

        lo = float(rule["min_value"])
        hi = float(rule["max_value"])
        flagged = selected[
            (selected["value"] < lo)
            | (selected["value"] > hi)
        ].copy()
        if flagged.empty:
            continue

        flagged["alert_category"] = rule["category"]
        flagged["threshold_min"] = lo
        flagged["threshold_max"] = hi
        flagged["threshold_expected_units"] = rule["expected_units"]
        flagged["anomaly_reason"] = flagged["value"].apply(
            lambda x: (
                f"value {x} is outside [{lo}, {hi}] "
                f"for {rule['category']}"
            )
        )
        flagged["anomaly_id"] = flagged.apply(
            _make_anomaly_id,
            axis=1,
        )

        for col in ANOMALY_COLUMNS:
            if col not in flagged.columns:
                flagged[col] = pd.NA
        anomalies.append(flagged[ANOMALY_COLUMNS])

    if not anomalies:
        out = pd.DataFrame(columns=ANOMALY_COLUMNS)
    else:
        out = pd.concat(anomalies, ignore_index=True)
        out = out.drop_duplicates(
            subset=["anomaly_id"],
            keep="first",
        ).reset_index(drop=True)

    return out, data_quality, sensor_error_rows


def _parse_recipients(value: str) -> list[str]:
    return [
        x.strip()
        for x in str(value).replace(";", ",").split(",")
        if x.strip()
    ]


def _build_email_body(
    new_anomalies: pd.DataFrame,
    max_rows: int,
    fetch_status: str,
    newly_silent_loggers: pd.DataFrame | None = None,
    recovered_loggers: list[str] | None = None,
) -> tuple[str, str]:
    """
    Build one combined operational alert email.

    The email can contain:
      1) physical-value anomalies;
      2) loggers that have newly stopped reporting;
      3) recovered loggers (informational, only when another alert is sent).
    """
    if newly_silent_loggers is None:
        newly_silent_loggers = pd.DataFrame(columns=LOGGER_STATUS_COLUMNS)
    recovered_loggers = recovered_loggers or []

    anomaly_total = len(new_anomalies)
    silence_total = len(newly_silent_loggers)
    sample = new_anomalies.head(int(max_rows))

    lines = [
        "SmartTAP Zentra operational alert",
        "",
        f"Daily fetch status: {fetch_status}",
        f"New anomalous readings: {anomaly_total}",
        f"Newly silent loggers: {silence_total}",
    ]

    if silence_total:
        lines.extend(["", "LOGGER SILENCE ALERTS:"])
        for _, row in newly_silent_loggers.iterrows():
            latest = row.get("latest_observation_utc")
            age = row.get("age_hours_at_fetch_end")
            age_text = (
                "unknown"
                if pd.isna(age)
                else f"{float(age):.2f} hours"
            )
            lines.append(
                f"- {row.get('logger_serial_number')}: "
                f"{row.get('reason')} "
                f"(latest observation: {latest}; age: {age_text})"
            )

    if anomaly_total:
        category_counts = (
            new_anomalies.groupby("alert_category", dropna=False)
            .size()
            .reset_index(name="count")
            .sort_values("count", ascending=False)
        )
        lines.extend(["", "ANOMALY BREAKDOWN:"])
        for _, row in category_counts.iterrows():
            lines.append(
                f"- {row['alert_category']}: {row['count']}"
            )

        lines.extend(["", "Sample anomalies:"])
        for i, (_, row) in enumerate(sample.iterrows(), 1):
            lines.append(
                f"{i}. {row.get('logger_serial_number')} "
                f"port {row.get('port_num')} | "
                f"{row.get('datetime')} | "
                f"{row.get('measurement')} = "
                f"{row.get('value')} {row.get('units')} "
                f"(allowed {row.get('threshold_min')} to "
                f"{row.get('threshold_max')})"
            )
        if anomaly_total > len(sample):
            lines.append(
                f"... and {anomaly_total - len(sample)} more."
            )

    if recovered_loggers:
        lines.extend([
            "",
            "Recovered loggers (informational): "
            + ", ".join(sorted(recovered_loggers)),
        ])

    text_body = "\n".join(lines)

    silence_rows = ""
    if silence_total:
        silence_rows = "".join(
            "<tr>"
            f"<td>{row.get('logger_serial_number')}</td>"
            f"<td>{row.get('latest_observation_utc')}</td>"
            f"<td>{row.get('age_hours_at_fetch_end')}</td>"
            f"<td>{row.get('reason')}</td>"
            "</tr>"
            for _, row in newly_silent_loggers.iterrows()
        )

    anomaly_rows = ""
    if anomaly_total:
        anomaly_rows = "".join(
            "<tr>"
            f"<td>{row.get('logger_serial_number')}</td>"
            f"<td>{row.get('port_num')}</td>"
            f"<td>{row.get('datetime')}</td>"
            f"<td>{row.get('measurement')}</td>"
            f"<td>{row.get('value')}</td>"
            f"<td>{row.get('units')}</td>"
            f"<td>{row.get('threshold_min')} to "
            f"{row.get('threshold_max')}</td>"
            "</tr>"
            for _, row in sample.iterrows()
        )

    silence_section = ""
    if silence_total:
        silence_section = f"""
        <h3>Logger silence alerts</h3>
        <p><strong>Newly silent loggers:</strong> {silence_total}</p>
        <table border="1" cellspacing="0" cellpadding="6">
        <tr><th>Logger</th><th>Latest observation UTC</th>
        <th>Age at fetch end (hours)</th><th>Reason</th></tr>
        {silence_rows}
        </table>
        """

    anomaly_section = ""
    if anomaly_total:
        anomaly_section = f"""
        <h3>Sensor-value anomalies</h3>
        <p><strong>New anomalous readings:</strong> {anomaly_total}</p>
        <table border="1" cellspacing="0" cellpadding="6">
        <tr><th>Logger</th><th>Port</th><th>Date/time</th>
        <th>Measurement</th><th>Value</th><th>Units</th>
        <th>Allowed range</th></tr>
        {anomaly_rows}
        </table>
        """

    recovery_section = ""
    if recovered_loggers:
        recovery_section = (
            "<p><strong>Recovered loggers:</strong> "
            + ", ".join(sorted(recovered_loggers))
            + "</p>"
        )

    html = f"""
    <h2>SmartTAP Zentra operational alert</h2>
    <p><strong>Daily fetch status:</strong> {fetch_status}</p>
    {silence_section}
    {anomaly_section}
    {recovery_section}
    """
    return text_body, html


def _send_resend_email(
    api_key: str,
    from_email: str,
    to_emails: list[str],
    subject: str,
    text: str,
    html: str,
) -> dict:
    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_email,
            "to": to_emails,
            "subject": subject,
            "text": text,
            "html": html,
        },
        timeout=30,
    )
    result = {
        "status_code": response.status_code,
        "ok": 200 <= response.status_code < 300,
        "response_text": response.text,
    }
    if not result["ok"]:
        raise RuntimeError(f"Resend email failed: {result}")
    return result


def detect_daily_zentra_anomalies_and_send_email(
    raw_prefix: str = "zentra_raw_backfill",
    state_prefix: str = "zentra_daily_update_state",
    latest_summary_file: str = "daily_update_summary_latest.json",
    alert_prefix: str = "zentra_anomaly_alerts",
    sent_ids_file: str = "sent_anomaly_ids.json",
    max_rows_in_email: int = 20,
    dry_run: Any = False,
    logger_silence_hours: float = 6.0,
    logger_silence_state_file: str = "logger_silence_state.json",
):
    """
    Daily SmartTAP operational monitor.

    Existing behavior is preserved:
      - checks physical sensor-value thresholds;
      - excludes sensor-error rows;
      - emails only anomalies that have not been emailed before.

    New behavior:
      - checks all expected loggers for loss of data;
      - a successful logger is "silent" when no selected-port rows were
        returned in the current daily fetch OR when its latest valid reading
        is older than logger_silence_hours at the fetch end time;
      - sends an email only when a logger becomes newly silent, so the same
        silent logger does not generate the same alert every day;
      - clears the silent state when a logger reports again, allowing a future
        stop to trigger a new alert;
      - failed API fetches are reported as unassessed rather than falsely
        classified as logger silence.

    Current daily workflow JSON remains compatible because all new arguments
    have defaults.
    """
    dry_run_bool = (
        dry_run
        if isinstance(dry_run, bool)
        else str(dry_run).strip().lower() in {
            "true", "1", "yes", "y"
        }
    )
    silence_hours = float(logger_silence_hours)
    if silence_hours <= 0:
        raise ValueError("logger_silence_hours must be > 0.")

    raw_paths, fetch_summary = _daily_paths_from_summary(
        state_prefix=state_prefix,
        latest_summary_file=latest_summary_file,
        raw_prefix=raw_prefix,
    )
    fetch_status = str(fetch_summary.get("status", "unknown"))
    failed_serials = set(
        str(x) for x in fetch_summary.get("failed_serials", [])
    )

    fetch_end = pd.to_datetime(
        fetch_summary.get("created_at_utc"),
        errors="coerce",
        utc=True,
    )
    if pd.isna(fetch_end):
        fetch_end = pd.Timestamp.now(tz="UTC")

    run_stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    all_anomalies = []
    data_quality = []
    sensor_error_rows = 0
    read_failures = []
    latest_observation_by_serial: dict[str, pd.Timestamp] = {}

    for path in raw_paths:
        try:
            raw = _read_raw_csv(path)
            raw = _standardize_raw_df(raw, path)

            serial = _extract_serial_from_source_path(path)
            if serial and not raw.empty:
                valid_times = raw["datetime"].dropna()
                if not valid_times.empty:
                    latest = valid_times.max()
                    previous = latest_observation_by_serial.get(serial)
                    if previous is None or latest > previous:
                        latest_observation_by_serial[serial] = latest

            anomalies, dq, err_count = _detect_in_df(raw)
            if not anomalies.empty:
                all_anomalies.append(anomalies)
            data_quality.extend(dq)
            sensor_error_rows += err_count
        except Exception as exc:
            read_failures.append({
                "source_file": path,
                "error": str(exc),
            })

    if read_failures:
        summary = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "failed_incomplete_input",
            "daily_fetch_status": fetch_status,
            "failed_serials_in_daily_fetch": sorted(failed_serials),
            "raw_files_expected": raw_paths,
            "read_failures": read_failures,
        }
        _put_json(
            summary,
            alert_prefix,
            "anomaly_alert_summary_latest.json",
        )
        raise RuntimeError(
            "Operational monitoring incomplete because one or more "
            f"daily raw files could not be read: {read_failures}"
        )

    # ---------------------------------------------------------------
    # Physical-value anomalies
    # ---------------------------------------------------------------
    anomalies_df = (
        pd.concat(all_anomalies, ignore_index=True)
        if all_anomalies
        else pd.DataFrame(columns=ANOMALY_COLUMNS)
    )
    if not anomalies_df.empty:
        anomalies_df = anomalies_df.drop_duplicates(
            subset=["anomaly_id"],
            keep="first",
        ).reset_index(drop=True)

    latest_file = "anomalies_latest.csv"
    timestamped_file = f"anomalies_{run_stamp}.csv"
    anomalies_df.to_csv(latest_file, index=False)
    anomalies_df.to_csv(timestamped_file, index=False)
    faasr_put_file(
        local_file=latest_file,
        remote_folder=alert_prefix,
        remote_file=latest_file,
    )
    faasr_put_file(
        local_file=timestamped_file,
        remote_folder=alert_prefix,
        remote_file=timestamped_file,
    )

    sent_ids = _load_sent_ids(
        alert_prefix,
        sent_ids_file,
    )
    new_df = (
        anomalies_df[
            ~anomalies_df["anomaly_id"].isin(sent_ids)
        ].copy()
        if not anomalies_df.empty
        else pd.DataFrame(columns=ANOMALY_COLUMNS)
    )

    new_file = "new_anomalies_latest.csv"
    new_df.to_csv(new_file, index=False)
    faasr_put_file(
        local_file=new_file,
        remote_folder=alert_prefix,
        remote_file=new_file,
    )

    # ---------------------------------------------------------------
    # Logger silence detection
    # ---------------------------------------------------------------
    prior_state = _load_logger_silence_state(
        alert_prefix,
        logger_silence_state_file,
    )
    prior_silent = set(
        str(x) for x in prior_state.get(
            "active_silent_loggers", []
        )
    )
    ever_seen = set(
        str(x) for x in prior_state.get(
            "ever_seen_loggers", []
        )
    )
    # Any logger with a valid observation in this run has now been proven active
    # at least once, which is required before we can later call it "stopped".
    ever_seen.update(latest_observation_by_serial.keys())

    serial_info = fetch_summary.get("serials", {})
    if not isinstance(serial_info, dict):
        serial_info = {}

    logger_rows = []
    detected_silent: set[str] = set()
    unassessed: set[str] = set()

    for serial in EXPECTED_LOGGERS:
        info = serial_info.get(serial, {})
        if not isinstance(info, dict):
            info = {}

        status = str(info.get("status", "missing")).strip().lower()
        rows_saved_raw = info.get("rows_saved_after_port_filter")
        try:
            rows_saved = int(rows_saved_raw)
        except Exception:
            rows_saved = 0

        latest = latest_observation_by_serial.get(serial)
        latest_text = (
            latest.isoformat()
            if latest is not None and not pd.isna(latest)
            else None
        )
        age_hours = None
        if latest is not None and not pd.isna(latest):
            age_hours = max(
                0.0,
                float((fetch_end - latest).total_seconds() / 3600.0),
            )

        logger_status = "active"
        reason = "Recent readings present."

        if status != "success":
            logger_status = "unassessed"
            reason = (
                "Daily API fetch did not complete successfully for this "
                "logger; logger silence cannot be determined from this run."
            )
            unassessed.add(serial)
        elif rows_saved <= 0 or not info.get("remote_path"):
            if serial in ever_seen:
                logger_status = "silent"
                reason = (
                    "This logger had previously produced data, but no "
                    "selected-port observations were returned in the latest "
                    "daily fetch window."
                )
                detected_silent.add(serial)
            else:
                logger_status = "no_data_never_seen"
                reason = (
                    "No selected-port observations were returned, but this "
                    "monitor has not yet recorded a prior valid reading from "
                    "this logger, so it is not classified as 'stopped'."
                )
        elif latest is None or pd.isna(latest):
            logger_status = "silent"
            reason = (
                "Rows were returned, but no valid observation datetime "
                "could be determined."
            )
            detected_silent.add(serial)
        elif age_hours is not None and age_hours > silence_hours:
            logger_status = "silent"
            reason = (
                f"Latest valid reading is {age_hours:.2f} hours old, "
                f"exceeding the {silence_hours:.2f}-hour silence threshold."
            )
            detected_silent.add(serial)

        logger_rows.append({
            "logger_serial_number": serial,
            "fetch_status": status,
            "rows_saved_after_port_filter": rows_saved,
            "latest_observation_utc": latest_text,
            "age_hours_at_fetch_end": age_hours,
            "logger_status": logger_status,
            "reason": reason,
        })

    logger_status_df = pd.DataFrame(
        logger_rows,
        columns=LOGGER_STATUS_COLUMNS,
    )

    # If a logger could not be assessed this run, preserve its previous
    # silence state instead of incorrectly declaring it recovered.
    next_active_silent = set(detected_silent)
    next_active_silent.update(prior_silent.intersection(unassessed))

    newly_silent = detected_silent - prior_silent
    recovered = sorted(
        serial
        for serial in prior_silent
        if serial not in next_active_silent
        and serial not in unassessed
    )

    new_silence_df = logger_status_df[
        logger_status_df["logger_serial_number"].isin(
            sorted(newly_silent)
        )
    ].copy()

    logger_status_latest = "logger_status_latest.csv"
    logger_status_timestamped = f"logger_status_{run_stamp}.csv"
    logger_status_df.to_csv(logger_status_latest, index=False)
    logger_status_df.to_csv(logger_status_timestamped, index=False)
    faasr_put_file(
        local_file=logger_status_latest,
        remote_folder=alert_prefix,
        remote_file=logger_status_latest,
    )
    faasr_put_file(
        local_file=logger_status_timestamped,
        remote_folder=alert_prefix,
        remote_file=logger_status_timestamped,
    )

    new_silence_file = "new_logger_silence_alerts_latest.csv"
    new_silence_df.to_csv(new_silence_file, index=False)
    faasr_put_file(
        local_file=new_silence_file,
        remote_folder=alert_prefix,
        remote_file=new_silence_file,
    )

    # ---------------------------------------------------------------
    # One combined email for new anomalies + newly silent loggers
    # ---------------------------------------------------------------
    email_needed = (not new_df.empty) or (not new_silence_df.empty)
    email = {
        "attempted": False,
        "sent": False,
        "dry_run": dry_run_bool,
        "detail": "No new anomalies or newly silent loggers.",
    }

    if email_needed:
        subject_parts = []
        if not new_silence_df.empty:
            subject_parts.append(
                f"{len(new_silence_df)} logger(s) stopped reporting"
            )
        if not new_df.empty:
            subject_parts.append(
                f"{len(new_df)} new anomalous reading(s)"
            )
        subject = "SmartTAP Zentra Alert: " + "; ".join(subject_parts)

        email_text, email_html = _build_email_body(
            new_df,
            max_rows=int(max_rows_in_email),
            fetch_status=fetch_status,
            newly_silent_loggers=new_silence_df,
            recovered_loggers=recovered,
        )

        if dry_run_bool:
            email = {
                "attempted": True,
                "sent": False,
                "dry_run": True,
                "subject": subject,
                "detail": "Dry run; email not sent and alert state not advanced.",
            }
        else:
            recipients = _parse_recipients(
                faasr_secret("ALERT_EMAIL_TO")
            )
            if not recipients:
                raise RuntimeError("ALERT_EMAIL_TO is empty.")

            response = _send_resend_email(
                api_key=faasr_secret("RESEND_API_KEY"),
                from_email=faasr_secret("ALERT_EMAIL_FROM"),
                to_emails=recipients,
                subject=subject,
                text=email_text,
                html=email_html,
            )
            email = {
                "attempted": True,
                "sent": True,
                "dry_run": False,
                "subject": subject,
                "to_count": len(recipients),
                "resend_response": response,
            }

            # Commit sent anomaly IDs only after successful Resend response.
            sent_ids.update(
                new_df["anomaly_id"].dropna().astype(str).tolist()
            )
            _save_sent_ids(
                alert_prefix,
                sent_ids_file,
                sent_ids,
            )

            # Commit silence transition state only after successful email.
            _save_logger_silence_state(
                alert_prefix,
                logger_silence_state_file,
                next_active_silent,
                ever_seen,
            )
    elif not dry_run_bool:
        # No new email is required, but commit recoveries/current state so a
        # future stop can trigger a fresh alert.
        _save_logger_silence_state(
            alert_prefix,
            logger_silence_state_file,
            next_active_silent,
            ever_seen,
        )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "threshold_version": THRESHOLD_VERSION,
        "daily_fetch_status": fetch_status,
        "failed_serials_in_daily_fetch": sorted(failed_serials),
        "raw_files_checked": raw_paths,
        "raw_file_count": len(raw_paths),
        "sensor_error_rows_excluded": int(sensor_error_rows),
        "data_quality_issue_count": len(data_quality),
        "data_quality_issues_sample": data_quality[:100],
        "total_anomalies_in_this_run": int(len(anomalies_df)),
        "new_anomalies_not_previously_emailed": int(len(new_df)),
        "logger_silence_hours": silence_hours,
        "silent_loggers_current": sorted(next_active_silent),
        "ever_seen_loggers": sorted(ever_seen),
        "newly_silent_loggers": sorted(newly_silent),
        "recovered_loggers": recovered,
        "unassessed_loggers": sorted(unassessed),
        "logger_status_latest_path": (
            f"{alert_prefix}/{logger_status_latest}"
        ),
        "new_logger_silence_alerts_latest_path": (
            f"{alert_prefix}/{new_silence_file}"
        ),
        "email": email,
    }
    _put_json(
        summary,
        alert_prefix,
        "anomaly_alert_summary_latest.json",
    )
    _put_json(
        summary,
        alert_prefix,
        f"anomaly_alert_summary_{run_stamp}.json",
    )

    faasr_log(
        "Daily operational monitoring complete. "
        f"anomalies={len(anomalies_df)}, "
        f"new_anomalies={len(new_df)}, "
        f"silent={len(next_active_silent)}, "
        f"newly_silent={len(newly_silent)}"
    )


# =====================================================================
# Weekly 12-configuration average email
# =====================================================================

def _download_config_csv(
    input_prefix: str,
    config_code: str,
) -> pd.DataFrame:
    filename = f"{config_code}.csv"
    if not _exists(input_prefix, filename):
        raise FileNotFoundError(
            f"Missing configuration CSV: {input_prefix}/{filename}"
        )
    local = f"_weekly_{config_code}.csv"
    faasr_get_file(
        local_file=local,
        remote_folder=input_prefix,
        remote_file=filename,
    )
    return pd.read_csv(local, low_memory=False)


def _standardize_weekly_config(
    df: pd.DataFrame,
    config_code: str,
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
        "logger_serial_number",
        "port_num",
        "measurement",
        "datetime",
        "Value",
        "Units",
    ]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(
            f"{config_code}: missing required columns {missing}"
        )

    for col in [
        "Location_code",
        "Shade_zone_code",
        "Irrigation_code",
        "logger_name",
        "port_description",
    ]:
        if col not in work.columns:
            work[col] = pd.NA

    work["config_code"] = config_code
    work["Value"] = pd.to_numeric(work["Value"], errors="coerce")
    work["port_num"] = pd.to_numeric(
        work["port_num"], errors="coerce"
    ).astype("Int64")
    work["measurement"] = (
        work["measurement"].astype("string").str.strip()
    )
    work["Units"] = work["Units"].astype("string").str.strip()
    work["logger_serial_number"] = (
        work["logger_serial_number"].astype("string").str.strip()
    )

    if "sub_sensor_index" in work.columns:
        work["sub_sensor_index"] = pd.to_numeric(
            work["sub_sensor_index"], errors="coerce"
        ).astype("Int64")
    if "sensor_sn" in work.columns:
        work["sensor_sn"] = (
            work["sensor_sn"].astype("string").str.strip()
        )
    if "mrid" in work.columns:
        work["mrid"] = work["mrid"].astype("string").str.strip()
    if "timestamp_utc" in work.columns:
        work["timestamp_utc"] = pd.to_numeric(
            work["timestamp_utc"], errors="coerce"
        )

    work["_dt_utc"] = pd.to_datetime(
        work["datetime"], errors="coerce", utc=True
    )

    invalid = work["Value"].isna() | work["_dt_utc"].isna()
    invalid_rows = int(invalid.sum())
    work = work[~invalid].copy()

    error_rows = 0
    if exclude_error_rows and "error_flag" in work.columns:
        err = work["error_flag"].map(_is_error_flag)
        error_rows = int(err.sum())
        work = work[~err].copy()

    work["_local_dt"] = work["_dt_utc"].dt.tz_convert(timezone_name)
    work["_local_date"] = work["_local_dt"].dt.date

    # User-requested exact duplicate rule:
    # duplicate only if Value AND Units match in addition to event fields.
    dedupe_candidates = [
        "logger_serial_number",
        "port_num",
        "mrid",
        "timestamp_utc",
        "_dt_utc",
        "measurement",
        "sub_sensor_index",
        "sensor_sn",
        "Value",
        "Units",
    ]
    dedupe_cols = [c for c in dedupe_candidates if c in work.columns]
    before = len(work)
    work = work.drop_duplicates(
        subset=dedupe_cols,
        keep="first",
    ).copy()
    duplicate_rows_removed = before - len(work)

    qa = {
        "input_rows": int(len(df)),
        "invalid_value_or_datetime_rows_removed": invalid_rows,
        "sensor_error_rows_removed": error_rows,
        "exact_duplicate_rows_removed": int(duplicate_rows_removed),
        "duplicate_rule": (
            "same available event fields + measurement + Value + Units; "
            "source_file ignored"
        ),
    }
    return work, qa


def _previous_complete_oregon_week(
    timezone_name: str,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Return the previous complete Monday-Sunday local week.

    start is Monday 00:00 local.
    end_exclusive is the next Monday 00:00 local.
    """
    local_now = pd.Timestamp.now(tz=timezone_name)
    current_monday = (
        local_now.normalize()
        - pd.Timedelta(days=int(local_now.weekday()))
    )
    start = current_monday - pd.Timedelta(days=7)
    return start, current_monday


def _build_weekly_averages_for_config(
    df: pd.DataFrame,
    config_code: str,
    timezone_name: str,
    week_start: pd.Timestamp,
    week_end_exclusive: pd.Timestamp,
    exclude_error_rows: bool,
) -> tuple[pd.DataFrame, dict]:
    work, qa = _standardize_weekly_config(
        df,
        config_code=config_code,
        timezone_name=timezone_name,
        exclude_error_rows=exclude_error_rows,
    )

    in_week = (
        (work["_local_dt"] >= week_start)
        & (work["_local_dt"] < week_end_exclusive)
    )
    work = work[in_week].copy()

    if work.empty:
        qa.update({
            "rows_in_week_after_cleaning": 0,
            "weekly_rows": 0,
        })
        return pd.DataFrame(columns=WEEKLY_OUTPUT_COLUMNS), qa

    group_base = [
        "config_code",
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

    # Same semantics as the monthly job: mean each local day first, then
    # average those daily means so a day with more raw observations does not
    # receive disproportionate weight.
    daily = (
        work.groupby(
            group_base + ["_local_date"],
            dropna=False,
        )
        .agg(Daily_Average=("Value", "mean"))
        .reset_index()
    )

    weekly = (
        daily.groupby(group_base, dropna=False)
        .agg(
            Weekly_Average=("Daily_Average", "mean"),
            days_with_data=("_local_date", "nunique"),
        )
        .reset_index()
    )
    weekly["coverage_ratio"] = weekly["days_with_data"] / 7.0
    weekly["week_start_local"] = week_start.strftime("%Y-%m-%d")
    weekly["week_end_local"] = (
        week_end_exclusive - pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    weekly = weekly[WEEKLY_OUTPUT_COLUMNS].copy()
    weekly = weekly.sort_values(
        [
            "config_code",
            "logger_serial_number",
            "port_num",
            "measurement",
            "Units",
        ]
    ).reset_index(drop=True)

    qa.update({
        "rows_in_week_after_cleaning": int(len(work)),
        "weekly_rows": int(len(weekly)),
    })
    return weekly, qa


def _build_weekly_email(
    weekly_df: pd.DataFrame,
    week_start: str,
    week_end: str,
    missing_configs: list[str],
    minimum_day_coverage_ratio: float,
) -> tuple[str, str]:
    low_coverage = weekly_df[
        weekly_df["coverage_ratio"] < float(minimum_day_coverage_ratio)
    ].copy() if not weekly_df.empty else weekly_df.copy()

    lines = [
        "SmartTAP weekly 12-configuration averages",
        "",
        f"Oregon local week: {week_start} through {week_end}",
        f"Weekly summary rows: {len(weekly_df)}",
        f"Low-coverage rows: {len(low_coverage)}",
    ]
    if missing_configs:
        lines.append(
            "Missing configuration files: "
            + ", ".join(sorted(missing_configs))
        )

    if not weekly_df.empty:
        lines.extend(["", "Weekly averages:"])
        for _, row in weekly_df.iterrows():
            lines.append(
                f"- {row['config_code']} | "
                f"{row['logger_serial_number']} | "
                f"port {row['port_num']} | "
                f"{row['measurement']}: "
                f"{row['Weekly_Average']:.6g} {row['Units']} "
                f"({int(row['days_with_data'])}/7 days)"
            )

    text_body = "\n".join(lines)

    rows_html = ""
    if not weekly_df.empty:
        rows_html = "".join(
            "<tr>"
            f"<td>{row['config_code']}</td>"
            f"<td>{row['logger_serial_number']}</td>"
            f"<td>{row['port_num']}</td>"
            f"<td>{row['measurement']}</td>"
            f"<td>{row['Weekly_Average']:.6g}</td>"
            f"<td>{row['Units']}</td>"
            f"<td>{int(row['days_with_data'])}/7</td>"
            "</tr>"
            for _, row in weekly_df.iterrows()
        )

    missing_html = (
        "<p><strong>Missing configuration files:</strong> "
        + ", ".join(sorted(missing_configs))
        + "</p>"
        if missing_configs
        else ""
    )

    html = f"""
    <h2>SmartTAP weekly 12-configuration averages</h2>
    <p><strong>Oregon local week:</strong> {week_start} through {week_end}</p>
    <p><strong>Summary rows:</strong> {len(weekly_df)}</p>
    <p><strong>Low-coverage rows:</strong> {len(low_coverage)}</p>
    {missing_html}
    <table border="1" cellspacing="0" cellpadding="6">
    <tr><th>Config</th><th>Logger</th><th>Port</th>
    <th>Measurement</th><th>Weekly average</th><th>Units</th>
    <th>Days with data</th></tr>
    {rows_html}
    </table>
    """
    return text_body, html


def send_weekly_12config_averages_email(
    input_prefix: str = "zentra_final_12_configs",
    output_prefix: str = "zentra_weekly_averages",
    timezone_name: str = "America/Los_Angeles",
    exclude_error_rows: Any = True,
    minimum_day_coverage_ratio: float = 0.8,
    state_file: str = "weekly_email_state.json",
    dry_run: Any = False,
    force_send: Any = False,
):
    """
    Compute and email the previous COMPLETE Monday-Sunday week for all
    12 SmartTAP configurations.

    Weekly average definition:
      raw observations
        -> exact duplicate removal (Value + Units included)
        -> Oregon-local daily mean
        -> mean of the seven available daily means for that week.

    The function writes auditable weekly CSV/JSON outputs to S3 and records the
    last week successfully emailed, preventing duplicate weekly emails.

    This function should be scheduled separately (recommended: every Monday)
    rather than added to the daily action chain.
    """
    dry_run_bool = (
        dry_run
        if isinstance(dry_run, bool)
        else str(dry_run).strip().lower() in {"true", "1", "yes", "y"}
    )
    force_send_bool = (
        force_send
        if isinstance(force_send, bool)
        else str(force_send).strip().lower() in {"true", "1", "yes", "y"}
    )
    exclude_errors_bool = (
        exclude_error_rows
        if isinstance(exclude_error_rows, bool)
        else str(exclude_error_rows).strip().lower()
        in {"true", "1", "yes", "y"}
    )

    week_start, week_end_exclusive = _previous_complete_oregon_week(
        timezone_name
    )
    week_start_s = week_start.strftime("%Y-%m-%d")
    week_end_s = (
        week_end_exclusive - pd.Timedelta(days=1)
    ).strftime("%Y-%m-%d")

    prior_state = {}
    if _exists(output_prefix, state_file):
        prior_state = _download_json(
            output_prefix,
            state_file,
            "_weekly_email_state.json",
        )

    if (
        not force_send_bool
        and prior_state.get("last_sent_week_start_local") == week_start_s
    ):
        faasr_log(
            f"Weekly averages email already sent for {week_start_s} "
            f"through {week_end_s}; skipping duplicate send."
        )
        return

    weekly_frames = []
    qa_by_config = {}
    missing_configs = []

    for config_code in CONFIG_CODES:
        try:
            df = _download_config_csv(
                input_prefix,
                config_code,
            )
            weekly, qa = _build_weekly_averages_for_config(
                df,
                config_code=config_code,
                timezone_name=timezone_name,
                week_start=week_start,
                week_end_exclusive=week_end_exclusive,
                exclude_error_rows=exclude_errors_bool,
            )
            qa_by_config[config_code] = qa
            if not weekly.empty:
                weekly_frames.append(weekly)
        except FileNotFoundError:
            missing_configs.append(config_code)
            qa_by_config[config_code] = {
                "status": "missing_config_file",
            }

    weekly_df = (
        pd.concat(weekly_frames, ignore_index=True)
        if weekly_frames
        else pd.DataFrame(columns=WEEKLY_OUTPUT_COLUMNS)
    )

    latest_csv = "weekly_averages_latest.csv"
    dated_csv = (
        f"weekly_averages_{week_start_s}_to_{week_end_s}.csv"
    )
    weekly_df.to_csv(latest_csv, index=False)
    weekly_df.to_csv(dated_csv, index=False)
    faasr_put_file(
        local_file=latest_csv,
        remote_folder=output_prefix,
        remote_file=latest_csv,
    )
    faasr_put_file(
        local_file=dated_csv,
        remote_folder=output_prefix,
        remote_file=dated_csv,
    )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "timezone_name": timezone_name,
        "week_start_local": week_start_s,
        "week_end_local": week_end_s,
        "average_method": "mean_of_oregon_local_daily_means",
        "duplicate_rule": (
            "same available event fields + measurement + Value + Units; "
            "source_file ignored"
        ),
        "minimum_day_coverage_ratio": float(
            minimum_day_coverage_ratio
        ),
        "weekly_rows": int(len(weekly_df)),
        "low_coverage_rows": int(
            (
                weekly_df["coverage_ratio"]
                < float(minimum_day_coverage_ratio)
            ).sum()
        ) if not weekly_df.empty else 0,
        "missing_configs": sorted(missing_configs),
        "config_qa": qa_by_config,
        "email": {
            "attempted": False,
            "sent": False,
            "dry_run": dry_run_bool,
        },
    }

    text_body, html_body = _build_weekly_email(
        weekly_df,
        week_start=week_start_s,
        week_end=week_end_s,
        missing_configs=missing_configs,
        minimum_day_coverage_ratio=float(
            minimum_day_coverage_ratio
        ),
    )

    subject = (
        f"SmartTAP Weekly Averages: "
        f"{week_start_s} to {week_end_s}"
    )

    if dry_run_bool:
        summary["email"] = {
            "attempted": True,
            "sent": False,
            "dry_run": True,
            "subject": subject,
            "detail": "Dry run; email not sent and weekly state not advanced.",
        }
    else:
        recipients = _parse_recipients(
            faasr_secret("ALERT_EMAIL_TO")
        )
        if not recipients:
            raise RuntimeError("ALERT_EMAIL_TO is empty.")

        response = _send_resend_email(
            api_key=faasr_secret("RESEND_API_KEY"),
            from_email=faasr_secret("ALERT_EMAIL_FROM"),
            to_emails=recipients,
            subject=subject,
            text=text_body,
            html=html_body,
        )

        summary["email"] = {
            "attempted": True,
            "sent": True,
            "dry_run": False,
            "subject": subject,
            "to_count": len(recipients),
            "resend_response": response,
        }

        _put_json(
            {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "last_sent_week_start_local": week_start_s,
                "last_sent_week_end_local": week_end_s,
            },
            output_prefix,
            state_file,
        )

    summary_latest = "weekly_summary_latest.json"
    summary_dated = (
        f"weekly_summary_{week_start_s}_to_{week_end_s}.json"
    )
    _put_json(
        summary,
        output_prefix,
        summary_latest,
    )
    _put_json(
        summary,
        output_prefix,
        summary_dated,
    )

    faasr_log(
        "Weekly 12-config average email complete. "
        f"week={week_start_s}..{week_end_s}; "
        f"rows={len(weekly_df)}; "
        f"missing_configs={len(missing_configs)}"
    )
