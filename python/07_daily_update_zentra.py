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

# Required Orchardgrass ports only.
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


def _serials(value: Any) -> list[str]:
    if value is None or str(value).strip().upper() == "ALL":
        return DEFAULT_SERIAL_NUMBERS
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    s = str(value).strip()
    if s.startswith("["):
        return [str(x).strip() for x in json.loads(s) if str(x).strip()]
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_ports(value: Any) -> list[int] | None:
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
    parsed = _parse_ports(ports)

    if parsed == []:
        return None
    if parsed is not None:
        return parsed

    mapped = LOGGER_PORTS.get(serial)
    if mapped is None:
        faasr_log(f"No port mapping found for {serial}; saving all ports.")
        return None
    return sorted({int(p) for p in mapped})


def _auth(token: str) -> dict[str, str]:
    token = str(token).strip()
    if not token.lower().startswith("token "):
        token = f"Token {token}"
    return {"Authorization": token}


def _zentra_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _exists(remote_folder: str, remote_file: str) -> bool:
    prefix = f"{remote_folder}/{remote_file}" if remote_folder else remote_file
    try:
        objs = faasr_get_folder_list(prefix=prefix)
    except Exception:
        return False
    return any(str(obj) == prefix or str(obj).endswith(prefix) or str(obj) == remote_file for obj in objs)


def _load_json(folder: str, filename: str) -> dict:
    if not _exists(folder, filename):
        return {}
    local = f"_download_{filename}"
    faasr_get_file(local_file=local, remote_folder=folder, remote_file=filename)
    with open(local, "r", encoding="utf-8") as f:
        return json.load(f)


def _upload_json(obj: dict, folder: str, filename: str) -> None:
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    faasr_put_file(local_file=filename, remote_folder=folder, remote_file=filename)


def _get_port_column(df: pd.DataFrame) -> str | None:
    for col in ("port_num", "port_number", "port"):
        if col in df.columns:
            return col
    return None


def _filter_to_ports(df: pd.DataFrame, serial: str, selected_ports: list[int] | None) -> pd.DataFrame:
    if df.empty or selected_ports is None:
        return df

    port_col = _get_port_column(df)
    if port_col is None:
        faasr_log(f"No port column found for {serial}; saving all rows for this page.")
        return df

    out = df.copy()
    out[port_col] = pd.to_numeric(out[port_col], errors="coerce")
    out = out[out[port_col].isin(selected_ports)].copy()

    faasr_log(
        f"Port filter for {serial}: kept {len(out)} of {len(df)} rows "
        f"for ports {selected_ports}."
    )
    return out


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
                    f"429 rate limit hit for {serial}, page {page_num}. "
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


def _window_for_serial(
    serial: str,
    end_dt: datetime,
    lookback_hours: int,
    state_prefix: str,
    buffer_hours: int,
) -> tuple[datetime, dict]:
    state_file = f"daily_update_state_{serial}.json"
    state = _load_json(state_prefix, state_file)

    min_start = end_dt - timedelta(hours=int(lookback_hours))

    last_successful_end = state.get("last_successful_end_utc")
    if last_successful_end:
        try:
            last_dt = pd.to_datetime(last_successful_end, utc=True).to_pydatetime()
            start_dt = min(last_dt - timedelta(hours=int(buffer_hours)), min_start)
            faasr_log(
                f"{serial}: using prior state. last_successful_end={last_dt.isoformat()}, "
                f"start={start_dt.isoformat()}"
            )
            return start_dt, state
        except Exception as exc:
            faasr_log(f"{serial}: could not parse saved state timestamp: {exc}")

    faasr_log(f"{serial}: no prior daily state; using lookback_hours={lookback_hours}")
    return min_start, state


def _upload_daily_csv(
    df: pd.DataFrame,
    raw_prefix: str,
    serial: str,
    start_dt: datetime,
    end_dt: datetime,
    selected_ports: list[int] | None,
) -> str:
    folder = f"{raw_prefix}/{serial}"
    port_label = "allports" if selected_ports is None else "ports-" + "-".join(str(p) for p in selected_ports)
    filename = f"daily_{serial}_{port_label}_{_stamp(start_dt)}_to_{_stamp(end_dt)}.csv"

    df.to_csv(filename, index=False)
    faasr_put_file(local_file=filename, remote_folder=folder, remote_file=filename)
    return f"{folder}/{filename}"


def fetch_daily_zentra_raw(
    serial_numbers: str = "ALL",
    lookback_hours: int = 26,
    buffer_hours: int = 1,
    raw_prefix: str = "zentra_raw_backfill",
    state_prefix: str = "zentra_daily_update_state",
    per_page: int = 2000,
    sleep_seconds: int = 65,
    sleep_between_serials: int = 2,
    server: str = "https://zentracloud.com",
    api_version: str = "v4",
    ports: str = "AUTO",
):
    """
    Daily incremental fetch for all Zentra loggers.

    Output:
      zentra_raw_backfill/<serial>/daily_<serial>_<ports>_<start>_to_<end>.csv
      zentra_daily_update_state/daily_update_state_<serial>.json
      zentra_daily_update_state/daily_update_summary_latest.json

    This function only fetches latest raw data and saves it into the same raw
    backfill folder used by Phase 2. The next Phase 2 steps update the 12 final
    configuration CSVs incrementally.
    """
    token = faasr_secret("ZENTRA_TOKEN")
    serial_list = _serials(serial_numbers)
    end_dt = datetime.now(timezone.utc)

    summary = {
        "created_at_utc": end_dt.isoformat(),
        "raw_prefix": raw_prefix,
        "state_prefix": state_prefix,
        "lookback_hours": int(lookback_hours),
        "buffer_hours": int(buffer_hours),
        "serials": {},
    }

    for serial_idx, serial in enumerate(serial_list):
        if serial_idx > 0 and int(sleep_between_serials) > 0:
            time.sleep(int(sleep_between_serials))

        selected_ports = _ports_for_serial(serial, ports)
        start_dt, state = _window_for_serial(
            serial=serial,
            end_dt=end_dt,
            lookback_hours=int(lookback_hours),
            state_prefix=state_prefix,
            buffer_hours=int(buffer_hours),
        )

        start_s = _zentra_dt(start_dt)
        end_s = _zentra_dt(end_dt)

        pages = []
        raw_rows = 0
        saved_rows = 0
        api_calls = 0
        page_num = 1

        while True:
            if page_num > 1:
                faasr_log(f"{serial}: sleeping {sleep_seconds}s before next page for same device.")
                time.sleep(int(sleep_seconds))

            faasr_log(
                f"Fetching daily update for {serial}: {start_s} to {end_s}, page={page_num}, "
                f"ports={selected_ports if selected_ports is not None else 'ALL'}"
            )

            df_raw = _fetch_page_retry(
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
            raw_rows += len(df_raw)

            df_filtered = _filter_to_ports(df_raw, serial, selected_ports)
            saved_rows += len(df_filtered)

            if not df_filtered.empty:
                pages.append(df_filtered)

            if len(df_raw) < int(per_page):
                break

            page_num += 1

        if pages:
            df_out = pd.concat(pages, ignore_index=True)
        else:
            df_out = pd.DataFrame()

        remote_path = None
        if not df_out.empty:
            remote_path = _upload_daily_csv(
                df=df_out,
                raw_prefix=raw_prefix,
                serial=serial,
                start_dt=start_dt,
                end_dt=end_dt,
                selected_ports=selected_ports,
            )
            faasr_log(f"{serial}: uploaded {len(df_out)} rows to {remote_path}")
        else:
            faasr_log(f"{serial}: no rows for selected ports in daily update window.")

        state.update({
            "serial": serial,
            "last_successful_start_utc": start_dt.isoformat(),
            "last_successful_end_utc": end_dt.isoformat(),
            "last_raw_rows_downloaded_from_api": int(raw_rows),
            "last_rows_saved_after_port_filter": int(saved_rows),
            "last_api_calls": int(api_calls),
            "last_remote_path": remote_path,
            "selected_ports": selected_ports,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        _upload_json(state, state_prefix, f"daily_update_state_{serial}.json")

        summary["serials"][serial] = {
            "start_utc": start_dt.isoformat(),
            "end_utc": end_dt.isoformat(),
            "raw_rows_downloaded_from_api": int(raw_rows),
            "rows_saved_after_port_filter": int(saved_rows),
            "api_calls": int(api_calls),
            "remote_path": remote_path,
            "selected_ports": selected_ports,
        }

    _upload_json(summary, state_prefix, "daily_update_summary_latest.json")
    timestamped_summary = f"daily_update_summary_{_stamp(end_dt)}.json"
    _upload_json(summary, state_prefix, timestamped_summary)
    faasr_log("Daily Zentra raw update complete.")
