import json
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import requests
from FaaSr_py.client.py_client_stubs import (
    faasr_log,
    faasr_put_file,
    faasr_secret,
)


def _parse_dt(value: str) -> datetime:
    return pd.to_datetime(value, utc=True).to_pydatetime()


def _zentra_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _auth(token: str) -> dict[str, str]:
    token = str(token).strip()
    if not token.lower().startswith("token "):
        token = f"Token {token}"
    return {"Authorization": token}


def _serials(value: Any) -> list[str]:
    """
    Accepts:
      "z6-19600,z6-19594"
      ["z6-19600", "z6-19594"]
      '["z6-19600", "z6-19594"]'
    """
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]

    s = str(value).strip()
    if s.startswith("["):
        return [str(x).strip() for x in json.loads(s) if str(x).strip()]

    return [x.strip() for x in s.split(",") if x.strip()]


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


def _fetch_page_with_retry(
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
                    f"429 rate limit for {serial}, page {page_num}. "
                    f"Sleeping {sleep_seconds}s before retry."
                )
                time.sleep(int(sleep_seconds))
                continue
            raise

        except requests.RequestException as exc:
            if attempt == 5:
                raise
            wait = min(30 * attempt, 120)
            faasr_log(
                f"Request failed for {serial}, page {page_num}, "
                f"attempt {attempt}/5: {exc}. Sleeping {wait}s."
            )
            time.sleep(wait)

    return pd.DataFrame()


def _upload_serial_csv(
    df: pd.DataFrame,
    raw_prefix: str,
    batch_name: str,
    serial: str,
    start_dt: datetime,
    end_dt: datetime,
) -> str:
    folder = f"{raw_prefix}/{serial}"
    filename = f"{batch_name}_{serial}_{_stamp(start_dt)}_to_{_stamp(end_dt)}.csv"

    df.to_csv(filename, index=False)

    faasr_put_file(
        local_file=filename,
        remote_folder=folder,
        remote_file=filename,
    )

    return f"{folder}/{filename}"


def _upload_json(obj: dict, remote_folder: str, remote_file: str) -> None:
    with open(remote_file, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)

    faasr_put_file(
        local_file=remote_file,
        remote_folder=remote_folder,
        remote_file=remote_file,
    )


def fetch_multiple_logger_raw(
    serial_numbers: str,
    start_date: str = "2026-04-29 00:00:00",
    end_date: str = "2026-04-30 00:00:00",
    raw_prefix: str = "zentra_raw_phase2_test",
    batch_name: str = "test_batch",
    per_page: int = 2000,
    sleep_seconds: int = 65,
    server: str = "https://zentracloud.com",
    api_version: str = "v4",
):
    """
    Fetch raw Zentra readings for multiple logger serial numbers and upload one CSV per logger to S3.

    S3 output:
      <raw_prefix>/<serial>/<batch_name>_<serial>_<start>_to_<end>.csv
      <raw_prefix>/_fetch_summaries/<batch_name>_summary.json

    This function only fetches and uploads raw data.
    It does NOT build the 12 configuration CSVs.
    """
    token = faasr_secret("ZENTRA_TOKEN")

    serial_list = _serials(serial_numbers)
    start_dt = _parse_dt(start_date)
    end_dt = _parse_dt(end_date)
    start_s = _zentra_dt(start_dt)
    end_s = _zentra_dt(end_dt)

    summary = {
        "batch_name": batch_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_date": start_s,
        "end_date": end_s,
        "raw_prefix": raw_prefix,
        "serials": [],
    }

    for serial_index, serial in enumerate(serial_list):
        if serial_index > 0:
            # Conservative pause between serials to avoid project/user/device limit issues.
            faasr_log(f"Sleeping {sleep_seconds}s before fetching next logger serial.")
            time.sleep(int(sleep_seconds))

        faasr_log(f"Starting fetch for serial={serial}, window={start_s} to {end_s}")

        page_num = 1
        pages = []
        api_calls = 0

        while True:
            faasr_log(f"Fetching serial={serial}, page={page_num}")
            df_page = _fetch_page_with_retry(
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
            api_calls += 1

            if not df_page.empty:
                pages.append(df_page)

            if len(df_page) < int(per_page):
                break

            # Same device next page: must respect one-call/minute/device.
            faasr_log(f"More pages possible for {serial}. Sleeping {sleep_seconds}s before next page.")
            time.sleep(int(sleep_seconds))
            page_num += 1

        if pages:
            df = pd.concat(pages, ignore_index=True)
        else:
            df = pd.DataFrame()

        remote_path = None
        if not df.empty:
            remote_path = _upload_serial_csv(
                df=df,
                raw_prefix=raw_prefix,
                batch_name=batch_name,
                serial=serial,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            faasr_log(f"Uploaded {len(df)} rows for {serial} to {remote_path}")
        else:
            faasr_log(f"No rows found for serial={serial}")

        summary["serials"].append(
            {
                "serial": serial,
                "rows": int(len(df)),
                "api_calls": int(api_calls),
                "remote_path": remote_path,
            }
        )

    _upload_json(
        summary,
        remote_folder=f"{raw_prefix}/_fetch_summaries",
        remote_file=f"{batch_name}_summary.json",
    )

    faasr_log(f"Finished batch {batch_name}. Uploaded raw CSVs for {len(serial_list)} serials.")
