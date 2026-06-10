
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
    match = re.search(r"z6-\d+", str(source_path))
    return match.group(0) if match else None


def _s3_object_exists(remote_folder: str, remote_file: str) -> bool:
    remote_folder = remote_folder.strip().rstrip("/")
    key = f"{remote_folder}/{remote_file}" if remote_folder else remote_file

    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=key))
    except Exception as exc:
        faasr_log(f"Could not check object existence for {key}: {exc}")
        return False

    for obj in objects:
        obj = str(obj).strip().lstrip("/")
        if obj == key or obj.endswith(f"/{remote_file}") or obj == remote_file:
            return True

    return False


def _download_json(remote_folder: str, remote_file: str, local_name: str) -> dict:
    faasr_get_file(local_file=local_name, remote_folder=remote_folder, remote_file=remote_file)
    with open(local_name, "r", encoding="utf-8") as f:
        return json.load(f)


def _put_json(obj: dict, remote_folder: str, remote_file: str) -> None:
    with open(remote_file, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    faasr_put_file(local_file=remote_file, remote_folder=remote_folder, remote_file=remote_file)


def _find_csv_paths_in_object(obj: Any, raw_prefix: str) -> list[str]:
    paths = []

    if isinstance(obj, dict):
        for value in obj.values():
            paths.extend(_find_csv_paths_in_object(value, raw_prefix))
    elif isinstance(obj, list):
        for item in obj:
            paths.extend(_find_csv_paths_in_object(item, raw_prefix))
    elif isinstance(obj, str):
        s = obj.strip().lstrip("/")
        if s.lower().endswith(".csv") and raw_prefix in s:
            paths.append(s)

    return paths


def _list_daily_raw_csvs(raw_prefix: str) -> list[str]:
    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=raw_prefix))
    except Exception as exc:
        faasr_log(f"Could not list fallback daily raw files under {raw_prefix}: {exc}")
        return []

    paths = []
    for obj in objects:
        s = str(obj).strip().lstrip("/")
        basename = s.split("/")[-1]
        if s.lower().endswith(".csv") and ("/daily_" in s or basename.startswith("daily_")):
            paths.append(s)

    return sorted(set(paths))


def _get_latest_daily_raw_paths(
    raw_prefix: str,
    state_prefix: str,
    latest_summary_file: str,
) -> list[str]:
    raw_prefix = raw_prefix.strip().rstrip("/")
    state_prefix = state_prefix.strip().rstrip("/")

    paths = []

    if _s3_object_exists(state_prefix, latest_summary_file):
        try:
            summary = _download_json(state_prefix, latest_summary_file, "_daily_update_summary_latest.json")
            paths = _find_csv_paths_in_object(summary, raw_prefix=raw_prefix)
            faasr_log(f"Found {len(paths)} raw CSV paths from {state_prefix}/{latest_summary_file}")
        except Exception as exc:
            faasr_log(f"Could not read latest daily update summary: {exc}")

    if not paths:
        paths = _list_daily_raw_csvs(raw_prefix)
        faasr_log(f"Fallback found {len(paths)} daily raw CSV paths under {raw_prefix}")

    return sorted(set(paths))


def _load_sent_ids(alert_prefix: str, sent_ids_file: str) -> set[str]:
    if not _s3_object_exists(alert_prefix, sent_ids_file):
        return set()

    try:
        data = _download_json(alert_prefix, sent_ids_file, "_sent_anomaly_ids.json")
        ids = data.get("sent_anomaly_ids", [])
        return set(str(x) for x in ids)
    except Exception as exc:
        faasr_log(f"Could not read sent anomaly ids; starting with empty set. Detail: {exc}")
        return set()


def _save_sent_ids(alert_prefix: str, sent_ids_file: str, sent_ids: set[str]) -> None:
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sent_anomaly_ids": sorted(sent_ids),
    }
    _put_json(payload, alert_prefix, sent_ids_file)


def _make_anomaly_id(row: pd.Series) -> str:
    parts = [
        str(row.get("logger_serial_number", "")),
        str(row.get("port_num", "")),
        str(row.get("datetime", "")),
        str(row.get("timestamp_utc", "")),
        str(row.get("measurement", "")),
        str(row.get("value", "")),
        str(row.get("units", "")),
        str(row.get("source_file", "")),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _standardize_raw_df(df: pd.DataFrame, source_path: str) -> pd.DataFrame:
    df = df.copy()

    for col in [
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
    ]:
        if col not in df.columns:
            df[col] = pd.NA

    df["logger_serial_number"] = _extract_serial_from_source_path(source_path)
    df["source_file"] = source_path
    df["port_num"] = pd.to_numeric(df["port_num"], errors="coerce").astype("Int64")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["measurement"] = df["measurement"].astype(str).str.strip()
    df["units"] = df["units"].astype(str).str.strip()

    return df


def _detect_anomalies_in_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)

    anomalies = []

    for measurement_norm, rule in THRESHOLDS.items():
        selected = df[df["measurement"].astype(str).str.strip().str.lower() == measurement_norm].copy()
        if selected.empty:
            continue

        min_value = float(rule["min_value"])
        max_value = float(rule["max_value"])

        flagged = selected[(selected["value"] < min_value) | (selected["value"] > max_value)].copy()
        if flagged.empty:
            continue

        flagged["alert_category"] = rule["category"]
        flagged["threshold_min"] = min_value
        flagged["threshold_max"] = max_value
        flagged["threshold_expected_units"] = rule["expected_units"]
        flagged["anomaly_reason"] = flagged["value"].apply(
            lambda x: f"value {x} is outside [{min_value}, {max_value}] for {rule['category']}"
        )

        anomalies.append(flagged)

    if not anomalies:
        return pd.DataFrame(columns=ANOMALY_COLUMNS)

    out = pd.concat(anomalies, ignore_index=True)
    out["anomaly_id"] = out.apply(_make_anomaly_id, axis=1)

    for col in ANOMALY_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[ANOMALY_COLUMNS]
    out = out.drop_duplicates(subset=["anomaly_id"], keep="first").reset_index(drop=True)

    return out


def _read_raw_csv(source_path: str) -> pd.DataFrame:
    remote_folder, remote_file = _remote_folder_and_file("", source_path)
    local_name = f"raw_for_anomaly_{remote_file}".replace("/", "_")
    faasr_get_file(local_file=local_name, remote_folder=remote_folder, remote_file=remote_file)
    return pd.read_csv(local_name)


def _parse_recipients(value: str) -> list[str]:
    recipients = []
    for item in str(value).replace(";", ",").split(","):
        item = item.strip()
        if item:
            recipients.append(item)
    return recipients


def _build_email_body(new_anomalies: pd.DataFrame, max_rows: int) -> tuple[str, str]:
    total = len(new_anomalies)
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
        "",
        "Breakdown:",
    ]

    for _, row in category_counts.iterrows():
        lines.append(f"- {row['alert_category']}: {row['count']}")

    lines.extend(["", "Sample anomalies:"])

    sample = new_anomalies.head(int(max_rows))
    for i, (_, row) in enumerate(sample.iterrows(), start=1):
        lines.append(
            f"{i}. {row.get('logger_serial_number')} port {row.get('port_num')} | "
            f"{row.get('datetime')} | {row.get('measurement')} = {row.get('value')} {row.get('units')} "
            f"(allowed: {row.get('threshold_min')} to {row.get('threshold_max')})"
        )

    if total > len(sample):
        lines.append(f"... and {total - len(sample)} more.")

    lines.extend([
        "",
        "The full anomaly CSV is saved in S3 under:",
        "zentra_anomaly_alerts/anomalies_latest.csv",
        "",
        "This email was generated automatically by the FaaSr daily Zentra workflow.",
    ])

    text = "\n".join(lines)

    html_rows = []
    for _, row in sample.iterrows():
        html_rows.append(
            "<tr>"
            f"<td>{row.get('logger_serial_number')}</td>"
            f"<td>{row.get('port_num')}</td>"
            f"<td>{row.get('datetime')}</td>"
            f"<td>{row.get('measurement')}</td>"
            f"<td>{row.get('value')}</td>"
            f"<td>{row.get('units')}</td>"
            f"<td>{row.get('threshold_min')} to {row.get('threshold_max')}</td>"
            "</tr>"
        )

    breakdown_items = "".join(
        f"<li>{row['alert_category']}: {row['count']}</li>"
        for _, row in category_counts.iterrows()
    )

    html = f"""
    <h2>SmartTAP Zentra anomaly alert</h2>
    <p><strong>New anomalous readings detected:</strong> {total}</p>
    <h3>Breakdown</h3>
    <ul>{breakdown_items}</ul>
    <h3>Sample anomalies</h3>
    <table border="1" cellspacing="0" cellpadding="6">
      <tr>
        <th>Logger</th><th>Port</th><th>Date/time</th><th>Measurement</th>
        <th>Value</th><th>Units</th><th>Allowed range</th>
      </tr>
      {''.join(html_rows)}
    </table>
    <p>The full anomaly CSV is saved in S3 under:<br>
    <code>zentra_anomaly_alerts/anomalies_latest.csv</code></p>
    <p>This email was generated automatically by the FaaSr daily Zentra workflow.</p>
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
    dry_run: str = "false",
):
    """
    Detect anomalies from raw files fetched in the latest daily update and send
    one Resend email if new anomalies are found.

    Detection uses only measurement + value.
    """
    raw_prefix = raw_prefix.strip().rstrip("/")
    state_prefix = state_prefix.strip().rstrip("/")
    alert_prefix = alert_prefix.strip().rstrip("/")
    dry_run_bool = str(dry_run).strip().lower() in ["true", "1", "yes", "y"]

    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    faasr_log("Starting daily Zentra anomaly detection.")

    raw_paths = _get_latest_daily_raw_paths(
        raw_prefix=raw_prefix,
        state_prefix=state_prefix,
        latest_summary_file=latest_summary_file,
    )

    all_anomalies = []

    for source_path in raw_paths:
        try:
            raw_df = _read_raw_csv(source_path)
            raw_df = _standardize_raw_df(raw_df, source_path=source_path)
            anomalies = _detect_anomalies_in_df(raw_df)
            if not anomalies.empty:
                all_anomalies.append(anomalies)
            faasr_log(f"Checked {source_path}: rows={len(raw_df)}, anomalies={len(anomalies)}")
        except Exception as exc:
            faasr_log(f"Could not process raw file for anomaly detection {source_path}: {exc}")

    if all_anomalies:
        anomalies_df = pd.concat(all_anomalies, ignore_index=True)
        anomalies_df = anomalies_df.drop_duplicates(subset=["anomaly_id"], keep="first").reset_index(drop=True)
    else:
        anomalies_df = pd.DataFrame(columns=ANOMALY_COLUMNS)

    latest_file = "anomalies_latest.csv"
    timestamped_file = f"anomalies_{run_timestamp}.csv"

    anomalies_df.to_csv(latest_file, index=False)
    faasr_put_file(local_file=latest_file, remote_folder=alert_prefix, remote_file=latest_file)

    anomalies_df.to_csv(timestamped_file, index=False)
    faasr_put_file(local_file=timestamped_file, remote_folder=alert_prefix, remote_file=timestamped_file)

    sent_ids = _load_sent_ids(alert_prefix, sent_ids_file)
    if anomalies_df.empty:
        new_anomalies_df = pd.DataFrame(columns=ANOMALY_COLUMNS)
    else:
        new_anomalies_df = anomalies_df[~anomalies_df["anomaly_id"].isin(sent_ids)].copy()

    new_latest_file = "new_anomalies_latest.csv"
    new_anomalies_df.to_csv(new_latest_file, index=False)
    faasr_put_file(local_file=new_latest_file, remote_folder=alert_prefix, remote_file=new_latest_file)

    email_result = {
        "attempted": False,
        "sent": False,
        "dry_run": dry_run_bool,
        "detail": "No new anomalies found.",
    }

    if not new_anomalies_df.empty:
        subject = f"SmartTAP Zentra Alert: {len(new_anomalies_df)} anomalous readings detected"
        text, html = _build_email_body(new_anomalies_df, max_rows=int(max_rows_in_email))

        if dry_run_bool:
            email_result = {
                "attempted": True,
                "sent": False,
                "dry_run": True,
                "detail": "Dry run enabled; email not sent.",
                "subject": subject,
            }
            faasr_log(f"[DRY RUN] Would send email: {subject}")
        else:
            api_key = faasr_secret("RESEND_API_KEY")
            from_email = faasr_secret("ALERT_EMAIL_FROM")
            to_raw = faasr_secret("ALERT_EMAIL_TO")
            to_emails = _parse_recipients(to_raw)

            if not to_emails:
                raise RuntimeError("ALERT_EMAIL_TO is empty. Cannot send anomaly email.")

            resend_response = _send_resend_email(
                api_key=api_key,
                from_email=from_email,
                to_emails=to_emails,
                subject=subject,
                text=text,
                html=html,
            )

            email_result = {
                "attempted": True,
                "sent": True,
                "dry_run": False,
                "subject": subject,
                "to_count": len(to_emails),
                "resend_response": resend_response,
            }

            sent_ids.update(new_anomalies_df["anomaly_id"].dropna().astype(str).tolist())
            _save_sent_ids(alert_prefix, sent_ids_file, sent_ids)

            faasr_log(f"Sent anomaly email: {subject}")

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_prefix": raw_prefix,
        "state_prefix": state_prefix,
        "alert_prefix": alert_prefix,
        "raw_files_checked": raw_paths,
        "thresholds": THRESHOLDS,
        "total_anomalies_in_this_run": int(len(anomalies_df)),
        "new_anomalies_not_previously_emailed": int(len(new_anomalies_df)),
        "anomalies_latest_path": f"{alert_prefix}/{latest_file}",
        "anomalies_timestamped_path": f"{alert_prefix}/{timestamped_file}",
        "new_anomalies_latest_path": f"{alert_prefix}/{new_latest_file}",
        "sent_ids_file": f"{alert_prefix}/{sent_ids_file}",
        "email": email_result,
    }

    _put_json(summary, alert_prefix, "anomaly_alert_summary_latest.json")
    _put_json(summary, alert_prefix, f"anomaly_alert_summary_{run_timestamp}.json")

    faasr_log("Anomaly detection summary:")
    faasr_log(json.dumps(summary, indent=2, sort_keys=True))
    faasr_log("Daily Zentra anomaly detection complete.")
