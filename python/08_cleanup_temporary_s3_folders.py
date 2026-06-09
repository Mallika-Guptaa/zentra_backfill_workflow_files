import json
from typing import Any

from FaaSr_py.client.py_client_stubs import (
    faasr_delete_file,
    faasr_get_folder_list,
    faasr_log,
)

# Only these temporary folders are allowed to be cleaned.
DEFAULT_CLEANUP_PREFIXES = [
    "zentra_phase2_staging/",
    "zentra_phase2_daily_staging/",
]

# These folders must never be deleted by this workflow.
PROTECTED_PREFIXES = [
    "zentra_raw_backfill/",
    "zentra_final_12_configs/",
    "zentra_daily_update_state/",
    "zentra_backfill_state/",
    "FaaSrLog/",
]


def _normalize_list_result(objects: Any) -> list[str]:
    if objects is None:
        return []
    if isinstance(objects, list):
        return [str(x) for x in objects]
    return [str(objects)]


def _parse_prefixes(value: Any) -> list[str]:
    """
    Accept either:
      - comma-separated string: "folder1,folder2"
      - JSON/list string: ["folder1", "folder2"]
      - Python list
    """
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
    for prefix in prefixes:
        prefix = str(prefix).strip().lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        if prefix:
            normalized.append(prefix)

    return normalized


def _is_safe_cleanup_prefix(prefix: str) -> bool:
    """
    Very conservative safety checks.

    This workflow is only for temporary staging folders.
    It must not delete raw data, final 12 CSVs, daily state, backfill state, or logs.
    """
    if not prefix or prefix in ["/", "*", ".", "./"]:
        return False

    for protected in PROTECTED_PREFIXES:
        if prefix.startswith(protected) or protected.startswith(prefix):
            return False

    return prefix in DEFAULT_CLEANUP_PREFIXES


def _remote_folder_and_file(default_folder: str, obj: str) -> tuple[str, str]:
    """
    Convert an S3 key into remote_folder + remote_file for faasr_delete_file.

    faasr_get_folder_list may return either:
      - full key: zentra_phase2_staging/raw_by_serial/file.csv
      - relative/bare key depending on FaaSr version
    """
    obj = str(obj).strip().lstrip("/")

    if "/" in obj:
        return "/".join(obj.split("/")[:-1]), obj.split("/")[-1]

    default_folder = default_folder.strip().rstrip("/")
    return default_folder, obj


def _delete_prefix(prefix: str, dry_run_bool: bool) -> dict:
    """
    Delete all objects under one safe staging prefix using FaaSr's datastore API.
    """
    if not _is_safe_cleanup_prefix(prefix):
        faasr_log(f"Skipping unsafe or protected cleanup prefix: {prefix}")
        return {
            "status": "skipped_unsafe_or_protected",
            "listed_objects": 0,
            "deleted_objects": 0,
            "errors": [],
        }

    try:
        objects = _normalize_list_result(faasr_get_folder_list(prefix=prefix))
    except Exception as exc:
        faasr_log(f"Could not list prefix {prefix}: {exc}")
        return {
            "status": "list_failed",
            "listed_objects": 0,
            "deleted_objects": 0,
            "errors": [str(exc)],
        }

    # Keep only objects that are actually under the target prefix.
    # This avoids deleting unrelated objects if a backend returns broader results.
    objects = [
        str(obj).strip()
        for obj in objects
        if str(obj).strip().lstrip("/").startswith(prefix)
    ]

    deleted = 0
    errors = []

    for obj in objects:
        remote_folder, remote_file = _remote_folder_and_file(prefix, obj)

        if dry_run_bool:
            faasr_log(f"[DRY RUN] Would delete {remote_folder}/{remote_file}")
            deleted += 1
            continue

        try:
            faasr_delete_file(remote_folder=remote_folder, remote_file=remote_file)
            deleted += 1
            faasr_log(f"Deleted {remote_folder}/{remote_file}")
        except Exception as exc:
            msg = f"Failed to delete {remote_folder}/{remote_file}: {exc}"
            faasr_log(msg)
            errors.append(msg)

    return {
        "status": "ok" if not errors else "completed_with_errors",
        "listed_objects": len(objects),
        "deleted_objects": deleted,
        "errors": errors,
    }


def cleanup_temporary_s3_folders(
    cleanup_prefixes: str = "zentra_phase2_staging,zentra_phase2_daily_staging",
    dry_run: str = "false",
):
    """
    Cleanup temporary S3 staging folders using the FaaSr datastore API.

    This version intentionally does NOT use boto3 and does NOT request
    S3PRIVATE_ACCESSKEY/S3PRIVATE_SECRETKEY as separate secrets.

    It relies on the workflow JSON's DefaultDataStore = S3PRIVATE, exactly like
    the working daily update and 12-config workflows.

    Deletes:
      - zentra_phase2_staging/
      - zentra_phase2_daily_staging/

    Never deletes:
      - zentra_raw_backfill/
      - zentra_final_12_configs/
      - zentra_daily_update_state/
      - zentra_backfill_state/
      - FaaSrLog/
    """
    dry_run_bool = str(dry_run).strip().lower() in ["true", "1", "yes", "y"]
    prefixes = _parse_prefixes(cleanup_prefixes)

    faasr_log(f"Starting temporary S3 cleanup. prefixes={prefixes}, dry_run={dry_run_bool}")

    summary = {
        "cleanup_prefixes": prefixes,
        "dry_run": dry_run_bool,
        "results": {},
    }

    for prefix in prefixes:
        summary["results"][prefix] = _delete_prefix(prefix, dry_run_bool)

    faasr_log("Cleanup summary:")
    faasr_log(json.dumps(summary, indent=2, sort_keys=True))
    faasr_log("Temporary S3 cleanup complete.")


def finish_cleanup_temporary_s3():
    """
    Terminal no-op node.

    This keeps the cleanup workflow DAG structure consistent with the working
    multi-action workflows and avoids one-node DAG validation issues.
    """
    faasr_log("Cleanup workflow finished successfully.")
