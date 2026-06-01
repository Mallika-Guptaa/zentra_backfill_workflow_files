import json
import time
from datetime import datetime, timedelta, timezone
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

DEFAULT_SERIAL_NUMBERS = [
    "z6-19600", "z6-12196", "z6-19602", "z6-19604", "z6-19597",
    "z6-19594", "z6-19599", "z6-12197", "z6-19595", "z6-19598",
    "z6-12202", "z6-19596", "z6-19603",
]

def _parse_dt(value: str | None, default: datetime | None = None) -> datetime:
    if value is None or str(value).strip() == "":
        if default is None:
            raise ValueError("Missing datetime value.")
        return default
    return pd.to_datetime(value, utc=True).to_pydatetime()

def _zentra_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def _serials(value: Any) -> list[str]:
    if value is None or str(value).strip().upper() == "ALL":
        return DEFAULT_SERIAL_NUMBERS
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if s.startswith("["):
        return [str(x).strip() for x in json.loads(s)]
    return [x.strip() for x in s.split(",") if x.strip()]

def _auth(token: str) -> dict[str, str]:
    token = token.strip()
    return {"Authorization": token if token.lower().startswith("token ") else f"Token {token}"}

def _exists(remote_folder: str, remote_file: str) -> bool:
    prefix = f"{remote_folder}/{remote_file}" if remote_folder else remote_file
    try:
        objs = faasr_get_folder_list(prefix=prefix)
    except Exception:
        return False
    return any(obj == prefix or obj.endswith(prefix) or obj == remote_file for obj in objs)

def _load_progress(folder: str, filename: str) -> dict:
    if not _exists(folder, filename):
        return {}
    local = "_progress_download.json"
    faasr_get_file(local_file=local, remote_folder=folder, remote_file=filename)
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f)

def _upload_json(obj: dict, folder: str, filename: str) -> None:
    local = filename
    with open(local, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    faasr_put_file(local_file=local, remote_folder=folder, remote_file=filename)

def _fetch_page(token, serial, start_s, end_s, page_num, per_page, api_version, server):
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
    r = requests.get(url, headers=_auth(token), params=params, timeout=60)
    if r.status_code == 429:
        raise RuntimeError("RATE_LIMIT_429")
    r.raise_for_status()
    payload = r.json()
    data = payload["data"]
    if isinstance(data, str):
        data = json.loads(data)
    try:
        return pd.DataFrame(**data)
    except TypeError:
        return pd.DataFrame(data)

def _fetch_page_retry(token, serial, start_s, end_s, page_num, per_page, api_version, server, sleep_seconds):
    for attempt in range(1, 6):
        try:
            return _fetch_page(token, serial, start_s, end_s, page_num, per_page, api_version, server)
        except RuntimeError as e:
            if "RATE_LIMIT_429" in str(e):
                faasr_log(f"429 rate limit hit for {serial}; sleeping {sleep_seconds}s")
                time.sleep(int(sleep_seconds))
                continue
            raise
        except requests.RequestException as e:
            if attempt == 5:
                raise
            wait = min(30 * attempt, 120)
            faasr_log(f"Request failed for {serial}, attempt {attempt}: {e}; sleeping {wait}s")
            time.sleep(wait)
    return pd.DataFrame()

def _upload_csv(df: pd.DataFrame, serial: str, start: datetime, end: datetime) -> str:
    folder = f"zentra_raw_backfill/{serial}"
    file = f"zentra_{serial}_{_stamp(start)}_to_{_stamp(end)}.csv"
    df.to_csv(file, index=False)
    faasr_put_file(local_file=file, remote_folder=folder, remote_file=file)
    return f"{folder}/{file}"

def backfill_zentra_history(
    serial_numbers: str = "ALL",
    start_date: str = "2024-04-04 00:00:00",
    end_date: str = "",
    chunk_days: int = 1,
    per_page: int = 2000,
    max_api_calls_per_run: int = 3,
    sleep_seconds: int = 65,
    server: str = "https://zentracloud.com",
    api_version: str = "v4",
    progress_file: str = "zentra_backfill_progress.json",
):
    """
    Resumable historical backfill. Re-run the FaaSr invoke repeatedly until done_all_devices=True
    in zentra_backfill_state/zentra_backfill_progress.json.
    """
    token = faasr_secret("ZENTRA_TOKEN")
    serial_list = _serials(serial_numbers)
    global_start = _parse_dt(start_date)
    global_end = _parse_dt(end_date, datetime.now(timezone.utc))
    chunk_delta = timedelta(days=int(chunk_days))
    state_folder = "zentra_backfill_state"

    progress = _load_progress(state_folder, progress_file) or {"devices": {}, "_meta": {}}
    progress["_meta"].update({
        "start_date": global_start.isoformat(),
        "last_requested_end_date": global_end.isoformat(),
        "chunk_days": int(chunk_days),
        "per_page": int(per_page),
    })

    calls_used = 0
    files_written = []

    for serial in serial_list:
        if calls_used >= int(max_api_calls_per_run):
            break

        device = progress["devices"].setdefault(serial, {
            "next_start": global_start.isoformat(),
            "done": False,
            "chunks_completed": 0,
            "rows_downloaded": 0,
        })

        if device.get("done"):
            continue

        next_start = _parse_dt(device.get("next_start"), global_start)

        while next_start < global_end and calls_used < int(max_api_calls_per_run):
            chunk_start = next_start
            chunk_end = min(chunk_start + chunk_delta, global_end)
            start_s, end_s = _zentra_dt(chunk_start), _zentra_dt(chunk_end)

            page_num = 1
            pages = []
            chunk_complete = True

            while True:
                if calls_used >= int(max_api_calls_per_run):
                    chunk_complete = False
                    break

                # Conservative project-wide/device-safe sleep between calls.
                if calls_used > 0:
                    faasr_log(f"Sleeping {sleep_seconds}s before next API call")
                    time.sleep(int(sleep_seconds))

                faasr_log(f"Fetching {serial}: {start_s} to {end_s}, page {page_num}")
                df_page = _fetch_page_retry(
                    token, serial, start_s, end_s, page_num, per_page,
                    api_version, server, sleep_seconds
                )
                calls_used += 1

                if not df_page.empty:
                    pages.append(df_page)

                if len(df_page) < int(per_page):
                    break
                page_num += 1

            if not chunk_complete:
                faasr_log("API-call budget reached before chunk completed. Progress not advanced for this chunk.")
                break

            df_chunk = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()

            if not df_chunk.empty:
                remote_path = _upload_csv(df_chunk, serial, chunk_start, chunk_end)
                files_written.append({"serial": serial, "path": remote_path, "rows": len(df_chunk)})
                device["rows_downloaded"] = int(device.get("rows_downloaded", 0)) + len(df_chunk)
                device["last_file"] = remote_path
            else:
                faasr_log(f"No rows for {serial}: {start_s} to {end_s}")

            next_start = chunk_end
            device["next_start"] = next_start.isoformat()
            device["chunks_completed"] = int(device.get("chunks_completed", 0)) + 1
            device["done"] = bool(next_start >= global_end)
            device["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
            _upload_json(progress, state_folder, progress_file)

        if calls_used >= int(max_api_calls_per_run):
            break

    done_all = all(progress["devices"].get(s, {}).get("done") for s in serial_list)
    summary = {
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "calls_used": calls_used,
        "files_written": files_written,
        "done_all_devices": done_all,
    }
    _upload_json(summary, state_folder, "zentra_backfill_last_run_summary.json")
    _upload_json(progress, state_folder, progress_file)
    faasr_log(f"Backfill run finished. done_all_devices={done_all}, calls_used={calls_used}")
    # Do not return the summary object to FaaSr. It is already saved to S3.
    # Returning a dict can cause FaaSr RPC /faasr-return 422 errors.



def initialize_parallel_backfill():
    """
    Start node for the parallel Zentra historical backfill workflow.

    This function does not fetch data. It only creates a clean root node
    for the FaaSr DAG before fan-out to the 13 logger-specific backfill actions.
    """
    faasr_log("Starting parallel Zentra historical backfill across logger serial numbers.")


def finish_backfill():
    """
    Terminal function used only to avoid a single-node FaaSr DAG.
    The actual work is done in backfill_zentra_history().
    """
    faasr_log("Zentra backfill workflow finished.")
