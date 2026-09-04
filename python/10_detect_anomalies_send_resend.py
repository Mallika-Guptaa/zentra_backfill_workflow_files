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

THRESHOLD_VERSION = "smarttap-thresholds-v1"

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
) -> tuple[str, str]:
    total = len(new_anomalies)
    sample = new_anomalies.head(int(max_rows))
    category_counts = (
        new_anomalies.groupby("alert_category", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
    )

    lines = [
        "SmartTAP Zentra anomaly alert",
        "",
        f"New anomalous readings detected: {total}",
        f"Daily fetch status: {fetch_status}",
        "",
        "Breakdown:",
    ]
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
    if total > len(sample):
        lines.append(f"... and {total-len(sample)} more.")

    text = "\n".join(lines)

    html_rows = "".join(
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
    breakdown = "".join(
        f"<li>{row['alert_category']}: {row['count']}</li>"
        for _, row in category_counts.iterrows()
    )
    html = f"""
    <h2>SmartTAP Zentra anomaly alert</h2>
    <p><strong>New anomalous readings:</strong> {total}</p>
    <p><strong>Daily fetch status:</strong> {fetch_status}</p>
    <ul>{breakdown}</ul>
    <table border="1" cellspacing="0" cellpadding="6">
    <tr><th>Logger</th><th>Port</th><th>Date/time</th>
    <th>Measurement</th><th>Value</th><th>Units</th>
    <th>Allowed range</th></tr>
    {html_rows}
    </table>
    """
    return text, html


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
):
    """
    Strict current-run anomaly detector.

    - Reads only raw paths named in daily_update_summary_latest.json.
    - Does not scan historical raw files as a fallback.
    - Checks measurement + normalized units + value.
    - Excludes raw sensor-error rows from physical threshold detection.
    - Logical anomaly IDs do not include source_file.
    - Any unreadable expected daily raw file makes the run incomplete/fail.
    """
    dry_run_bool = (
        dry_run
        if isinstance(dry_run, bool)
        else str(dry_run).strip().lower() in {
            "true", "1", "yes", "y"
        }
    )

    raw_paths, fetch_summary = _daily_paths_from_summary(
        state_prefix=state_prefix,
        latest_summary_file=latest_summary_file,
        raw_prefix=raw_prefix,
    )
    fetch_status = str(fetch_summary.get("status", "unknown"))
    failed_serials = fetch_summary.get("failed_serials", [])

    run_stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    all_anomalies = []
    data_quality = []
    sensor_error_rows = 0
    read_failures = []

    for path in raw_paths:
        try:
            raw = _read_raw_csv(path)
            raw = _standardize_raw_df(raw, path)
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
            "failed_serials_in_daily_fetch": failed_serials,
            "raw_files_expected": raw_paths,
            "read_failures": read_failures,
        }
        _put_json(
            summary,
            alert_prefix,
            "anomaly_alert_summary_latest.json",
        )
        raise RuntimeError(
            "Anomaly detection incomplete because one or more "
            f"daily raw files could not be read: {read_failures}"
        )

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
    new_df = anomalies_df[
        ~anomalies_df["anomaly_id"].isin(sent_ids)
    ].copy() if not anomalies_df.empty else pd.DataFrame(
        columns=ANOMALY_COLUMNS
    )

    new_file = "new_anomalies_latest.csv"
    new_df.to_csv(new_file, index=False)
    faasr_put_file(
        local_file=new_file,
        remote_folder=alert_prefix,
        remote_file=new_file,
    )

    email = {
        "attempted": False,
        "sent": False,
        "dry_run": dry_run_bool,
        "detail": "No new anomalies found.",
    }

    if not new_df.empty:
        subject = (
            f"SmartTAP Zentra Alert: "
            f"{len(new_df)} new anomalous readings"
        )
        text, html = _build_email_body(
            new_df,
            max_rows=int(max_rows_in_email),
            fetch_status=fetch_status,
        )

        if dry_run_bool:
            email = {
                "attempted": True,
                "sent": False,
                "dry_run": True,
                "subject": subject,
                "detail": "Dry run; email not sent.",
            }
        else:
            recipients = _parse_recipients(
                faasr_secret("ALERT_EMAIL_TO")
            )
            if not recipients:
                raise RuntimeError(
                    "ALERT_EMAIL_TO is empty."
                )
            response = _send_resend_email(
                api_key=faasr_secret("RESEND_API_KEY"),
                from_email=faasr_secret("ALERT_EMAIL_FROM"),
                to_emails=recipients,
                subject=subject,
                text=text,
                html=html,
            )
            email = {
                "attempted": True,
                "sent": True,
                "dry_run": False,
                "subject": subject,
                "to_count": len(recipients),
                "resend_response": response,
            }

            # Commit sent IDs only after successful Resend response.
            sent_ids.update(
                new_df["anomaly_id"].dropna().astype(str).tolist()
            )
            _save_sent_ids(
                alert_prefix,
                sent_ids_file,
                sent_ids,
            )

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "threshold_version": THRESHOLD_VERSION,
        "daily_fetch_status": fetch_status,
        "failed_serials_in_daily_fetch": failed_serials,
        "raw_files_checked": raw_paths,
        "raw_file_count": len(raw_paths),
        "sensor_error_rows_excluded": int(sensor_error_rows),
        "data_quality_issue_count": len(data_quality),
        "data_quality_issues_sample": data_quality[:100],
        "total_anomalies_in_this_run": int(len(anomalies_df)),
        "new_anomalies_not_previously_emailed": int(len(new_df)),
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
        "Daily anomaly detection complete. "
        f"anomalies={len(anomalies_df)}, new={len(new_df)}"
    )
