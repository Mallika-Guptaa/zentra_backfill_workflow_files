import json
from datetime import datetime, timezone, timedelta
from typing import Any

import boto3
from FaaSr_py.client.py_client_stubs import faasr_log, faasr_secret


DEFAULT_CLEANUP_PREFIXES = [
    "zentra_phase2_staging/",
    "zentra_phase2_daily_staging/",
]


PROTECTED_PREFIXES = [
    "zentra_raw_backfill/",
    "zentra_final_12_configs/",
    "zentra_daily_update_state/",
    "zentra_backfill_state/",
]


def _parse_prefixes(value: Any) -> list[str]:
    if value is None:
        prefixes = DEFAULT_CLEANUP_PREFIXES
    elif isinstance(value, list):
        prefixes = [str(x).strip() for x in value if str(x).strip()]
    else:
        s = str(value).strip()
        if not s:
            prefixes = DEFAULT_CLEANUP_PREFIXES
        elif s.startswith("["):
            prefixes = [str(x).strip() for x in json.loads(s) if str(x).strip()]
        else:
            prefixes = [x.strip() for x in s.split(",") if x.strip()]

    normalized = []
    for p in prefixes:
        p = p.lstrip("/")
        if p and not p.endswith("/"):
            p += "/"
        if p:
            normalized.append(p)
    return normalized


def _is_safe_prefix(prefix: str) -> bool:
    if not prefix or prefix in ["*", "/"]:
        return False

    for protected in PROTECTED_PREFIXES:
        if prefix.startswith(protected) or protected.startswith(prefix):
            return False

    return True


def _client(region: str, access_key_secret: str, secret_key_secret: str):
    access_key = faasr_secret(access_key_secret)
    secret_key = faasr_secret(secret_key_secret)

    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _delete_batch(s3, bucket: str, objects: list[dict], dry_run: bool) -> int:
    if not objects:
        return 0

    if dry_run:
        return len(objects)

    response = s3.delete_objects(
        Bucket=bucket,
        Delete={
            "Objects": [{"Key": obj["Key"]} for obj in objects],
            "Quiet": True,
        },
    )
    deleted = response.get("Deleted", [])
    errors = response.get("Errors", [])

    if errors:
        faasr_log(f"Delete batch had errors: {errors}")

    return len(deleted)


def cleanup_temporary_s3_folders(
    bucket: str = "faasr-bucket-smarttap-private",
    region: str = "us-east-1",
    cleanup_prefixes: str = "zentra_phase2_staging,zentra_phase2_daily_staging",
    retention_hours: int = 4,
    dry_run: str = "false",
    access_key_secret: str = "S3PRIVATE_ACCESSKEY",
    secret_key_secret: str = "S3PRIVATE_SECRETKEY",
):
    """
    Delete temporary S3 staging folders after the main Zentra workflows are done.

    Default folders deleted:
      - zentra_phase2_staging/
      - zentra_phase2_daily_staging/

    Protected folders that this function will not delete:
      - zentra_raw_backfill/
      - zentra_final_12_configs/
      - zentra_daily_update_state/
      - zentra_backfill_state/

    retention_hours prevents deleting files from a workflow that may still be running.
    Example:
      retention_hours=4 means only staging objects older than 4 hours are deleted.
    """
    dry = str(dry_run).strip().lower() in ["true", "1", "yes", "y"]
    prefixes = _parse_prefixes(cleanup_prefixes)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(retention_hours))

    faasr_log(
        f"Starting cleanup. bucket={bucket}, prefixes={prefixes}, "
        f"retention_hours={retention_hours}, cutoff={cutoff.isoformat()}, dry_run={dry}"
    )

    s3 = _client(region, access_key_secret, secret_key_secret)

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "bucket": bucket,
        "region": region,
        "retention_hours": int(retention_hours),
        "dry_run": dry,
        "prefixes": {},
    }

    for prefix in prefixes:
        if not _is_safe_prefix(prefix):
            faasr_log(f"Skipping unsafe/protected prefix: {prefix}")
            summary["prefixes"][prefix] = {
                "status": "skipped_unsafe_or_protected",
                "listed_objects": 0,
                "eligible_for_delete": 0,
                "deleted": 0,
            }
            continue

        paginator = s3.get_paginator("list_objects_v2")

        listed = 0
        eligible = 0
        deleted = 0
        batch = []

        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                listed += 1
                key = obj["Key"]
                last_modified = obj["LastModified"]

                if last_modified <= cutoff:
                    eligible += 1
                    batch.append({"Key": key})

                if len(batch) >= 1000:
                    deleted += _delete_batch(s3, bucket, batch, dry)
                    batch = []

        if batch:
            deleted += _delete_batch(s3, bucket, batch, dry)

        summary["prefixes"][prefix] = {
            "status": "ok",
            "listed_objects": listed,
            "eligible_for_delete": eligible,
            "deleted": deleted,
        }

        faasr_log(
            f"Cleanup prefix={prefix}: listed={listed}, "
            f"eligible={eligible}, deleted={deleted}, dry_run={dry}"
        )

    faasr_log("Cleanup summary:")
    faasr_log(json.dumps(summary, indent=2, sort_keys=True))
    faasr_log("Temporary S3 cleanup complete.")

def finish_cleanup_temporary_s3():
    """
    Small terminal node for FaaSr DAG validation.

    Some FaaSr versions do not like a one-action workflow with InvokeNext=[].
    This no-op finish node gives the workflow a clear terminal state.
    """
    faasr_log("Cleanup workflow finished successfully.")

