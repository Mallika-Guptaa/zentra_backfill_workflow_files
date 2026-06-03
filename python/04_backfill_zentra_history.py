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

# Logger serials used in the Orchardgrass trial.
DEFAULT_SERIAL_NUMBERS = [
    "z6-19600", "z6-12196", "z6-19602", "z6-19604", "z6-19597",
    "z6-19594", "z6-19599", "z6-12197", "z6-19595", "z6-19598",
    "z6-12202", "z6-19596", "z6-19603",
]

# Ports that are actually needed according to the Orchardgrass Trial Information document.
# The public Zentra get_readings API is queried by logger serial number; this code filters
# the returned rows to these ports before saving to S3.
LOGGER_PORTS = {
    "z6-19600": [2, 4, 5],          # NEWAg #7
    "z6-12196": [3, 5, 6],          # NEWAg #13
    "z6-19602": [2],                # NEWAg #9
    "z6-19604": [3],                # NEWAg #11
    "z6-19597": [2, 3],             # NEWAg #4
    "z6-19594": [2, 3, 4, 5, 6],    # NEWAg #1
    "z6-19599": [3],                # NEWAg #6
    "z6-12197": [2, 3, 4],          # NEWAg #15
    "z6-19595": [2, 3, 4, 5, 6],    # NEWAg #2
    "z6-19598": [3, 4],             # NEWAg #5
    "z6-12202": [2, 3, 4],          # NEWAg #14
    "z6-19596": [2, 3, 4, 5, 6],    # NEWAg #3
    "z6-19603": [2, 3, 4, 5],       # NEWAg #10
}


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


def _parse_ports(value: Any) -> list[int] | None:
    """
    Accepts:
      None / "" / "AUTO" -> use LOGGER_PORTS mapping.
      "ALL" -> no port filtering.
      "2,4,5" -> explicit ports.
      [2, 4, 5] or "[2, 4, 5]" -> explicit ports.
    Returns:
      None when AUTO should be resolved later.
      [] as the marker for ALL/no filtering.
      list[int] for explicit ports.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return sorted({int(x) for x in value})
    s = str(value).strip()
    if s == "" or s.upper() == "AUTO":
        return None
    if s.upper() == "ALL":
        return []
    if s.startswith("["):
        return sorted({int(x) for x in json.loads(s)})
    return sorted({int(x.strip()) for x in s.split(",") if x.strip()})


def _ports_for_serial(serial: str, ports: Any = "AUTO") -> list[int] | None:
    """
    Returns selected ports for a serial.
    None means no filtering; list[int] means save only those ports.
    """
    parsed = _parse_ports(ports)
    if parsed == []:
        return None
    if parsed is not None:
        return parsed
    mapped_ports = LOGGER_PORTS.get(serial)
    if mapped_ports is None:
        faasr_log(f"No port mapping found for {serial}; saving all ports.")
        return None
    return sorted({int(p) for p in mapped_ports})


def _get_port_column(df: pd.DataFrame) -> str | None:
    for col in ("port_num", "port_number", "port"):
        if col in df.columns:
            return col
    return None


def _filter_to_ports(df: pd.DataFrame, serial: str, ports: list[int] | None) -> pd.DataFrame:
    """
    Keep only selected ports before saving to S3.
    If ports is None, save all rows.
    """
    if df.empty or ports is None:
        return df
    port_col = _get_port_column(df)
    if port_col is None:
        faasr_log(
            f"Could not find a port column for {serial}; expected one of "
            f"port_num, port_number, port. Saving all rows for this page."
        )
        return df
    filtered = df.copy()
    filtered[port_col] = pd.to_numeric(filtered[port_col], errors="coerce")
    filtered = filtered[filtered[port_col].isin(ports)].copy()
    faasr_log(f"Port filter for {serial}: kept {len(filtered)} of {len(df)} rows for ports {ports}.")
    return filtered


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


def _upload_csv(df: pd.DataFrame, serial: str, start: datetime, end: datetime, ports: list[int] | None = None) -> str:
    folder = f"zentra_raw_backfill/{serial}"
    port_label = "allports" if ports is None else "ports-" + "-".join(str(p) for p in ports)
    file = f"zentra_{serial}_{port_label}_{_stamp(start)}_to_{_stamp(end)}.csv"
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
    ports: str = "AUTO",
):
    """
    Resumable historical backfill.

    Port-aware behavior:
    - ports="AUTO" uses LOGGER_PORTS from the Orchardgrass mapping.
    - ports="ALL" saves all ports.
    - ports="2,4,5" saves only explicitly listed ports.

    The official Zentra get_readings API is queried by device serial number.
    It does not expose a documented port filter in the public parameter list,
    so this code filters port rows immediately after each API page is downloaded
    and before the CSV is saved to S3.
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
        "ports_argument": ports,
        "port_filtering": "filtered_after_api_page_before_s3_upload",
    })

    calls_used = 0
    files_written = []

    for serial in serial_list:
        if calls_used >= int(max_api_calls_per_run):
            break

        selected_ports = _ports_for_serial(serial, ports=ports)
        faasr_log(f"Selected ports for {serial}: {selected_ports if selected_ports is not None else 'ALL'}")

        device = progress["devices"].setdefault(serial, {
            "next_start": global_start.isoformat(),
            "done": False,
            "chunks_completed": 0,
            "rows_downloaded": 0,
            "rows_saved_after_port_filter": 0,
            "selected_ports": selected_ports,
        })
        device["selected_ports"] = selected_ports

        if device.get("done"):
            continue

        next_start = _parse_dt(device.get("next_start"), global_start)

        while next_start < global_end and calls_used < int(max_api_calls_per_run):
            chunk_start = next_start
            chunk_end = min(chunk_start + chunk_delta, global_end)
            start_s, end_s = _zentra_dt(chunk_start), _zentra_dt(chunk_end)

            page_num = 1
            filtered_pages = []
            raw_rows_this_chunk = 0
            saved_rows_this_chunk = 0
            chunk_complete = True

            while True:
                if calls_used >= int(max_api_calls_per_run):
                    chunk_complete = False
                    break

                if calls_used > 0:
                    faasr_log(f"Sleeping {sleep_seconds}s before next API call")
                    time.sleep(int(sleep_seconds))

                faasr_log(
                    f"Fetching {serial}: {start_s} to {end_s}, page {page_num}, "
                    f"ports={selected_ports if selected_ports is not None else 'ALL'}"
                )
                df_page_raw = _fetch_page_retry(
                    token, serial, start_s, end_s, page_num, per_page,
                    api_version, server, sleep_seconds
                )
                calls_used += 1
                raw_rows_this_chunk += len(df_page_raw)

                df_page_filtered = _filter_to_ports(df_page_raw, serial=serial, ports=selected_ports)
                saved_rows_this_chunk += len(df_page_filtered)

                if not df_page_filtered.empty:
                    filtered_pages.append(df_page_filtered)

                # Stop based on raw page length, not filtered page length.
                # Otherwise later pages could be skipped when selected ports are sparse.
                if len(df_page_raw) < int(per_page):
                    break
                page_num += 1

            if not chunk_complete:
                faasr_log("API-call budget reached before chunk completed. Progress not advanced for this chunk.")
                break

            df_chunk = pd.concat(filtered_pages, ignore_index=True) if filtered_pages else pd.DataFrame()

            if not df_chunk.empty:
                remote_path = _upload_csv(df_chunk, serial, chunk_start, chunk_end, ports=selected_ports)
                files_written.append({
                    "serial": serial,
                    "path": remote_path,
                    "raw_rows_downloaded_from_api": raw_rows_this_chunk,
                    "rows_saved_after_port_filter": len(df_chunk),
                    "selected_ports": selected_ports,
                })
                device["rows_downloaded"] = int(device.get("rows_downloaded", 0)) + raw_rows_this_chunk
                device["rows_saved_after_port_filter"] = int(device.get("rows_saved_after_port_filter", 0)) + len(df_chunk)
                device["last_file"] = remote_path
            else:
                faasr_log(
                    f"No rows for selected ports for {serial}: {start_s} to {end_s}. "
                    f"Raw rows from API: {raw_rows_this_chunk}; selected ports: "
                    f"{selected_ports if selected_ports is not None else 'ALL'}"
                )
                device["rows_downloaded"] = int(device.get("rows_downloaded", 0)) + raw_rows_this_chunk

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
        "ports_argument": ports,
    }
    _upload_json(summary, state_folder, "zentra_backfill_last_run_summary.json")
    _upload_json(progress, state_folder, progress_file)
    faasr_log(f"Backfill run finished. done_all_devices={done_all}, calls_used={calls_used}")
    # Do not return the summary object to FaaSr. It is already saved to S3.


def initialize_parallel_backfill():
    """
    Start node for the parallel Zentra historical backfill workflow.
    """
    faasr_log("Starting parallel Zentra historical backfill across logger serial numbers.")


def finish_backfill():
    """
    Terminal function used only to avoid a single-node FaaSr DAG.
    """
    faasr_log("Zentra backfill workflow finished.")
