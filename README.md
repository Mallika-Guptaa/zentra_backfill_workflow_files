# Zentra SmartTAP Data Workflows

This repository contains the Python functions used by the FaaSr workflows for the SmartTAP / Orchardgrass Zentra data pipeline.

The pipeline collects data from Zentra loggers, stores raw data in S3, forms the 12 final Orchardgrass configuration CSVs, checks for anomalous readings, sends email alerts using Resend, builds monthly averages, and cleans temporary staging folders.

---

## 1. Project Overview

The system is designed around three main data layers:

```text
Zentra API
   ↓
Raw logger data in S3
   ↓
12 final Orchardgrass configuration CSVs
   ↓
Monthly averages + anomaly alerts
```

The final 12 CSVs are created using the Orchardgrass trial mapping:

```text
Location + Shade Zone + Irrigation
```

The key matching rule for assigning raw rows into the correct configuration is:

```text
logger_serial_number + port_num
```

Important detail:

- `port_num` comes directly from the raw Zentra CSV.
- `logger_serial_number` is not present inside the raw CSV, so it is derived from the S3 folder path or raw filename, such as `z6-19594`.

---

## 2. Main S3 Locations

The workflows use the private S3 datastore:

```text
DataStore: S3PRIVATE
Bucket: faasr-bucket-smarttap-private
Region: us-east-1
```

Main folders:

```text
zentra_raw_backfill/
zentra_final_12_configs/
zentra_daily_update_state/
zentra_anomaly_alerts/
Zentra_monthly_averages/
zentra_phase2_staging/
zentra_phase2_daily_staging/
FaaSrLog/
```

| S3 Folder | Purpose |
|---|---|
| `zentra_raw_backfill/` | Stores raw Zentra CSV files by logger serial number |
| `zentra_final_12_configs/` | Stores the final 12 configuration CSVs |
| `zentra_daily_update_state/` | Stores state and summaries for daily incremental updates |
| `zentra_anomaly_alerts/` | Stores anomaly CSVs, summaries, and sent-alert IDs |
| `Zentra_monthly_averages/` | Stores monthly average CSVs for each configuration |
| `zentra_phase2_staging/` | Temporary staging folder for full rebuild / phase 2 processing |
| `zentra_phase2_daily_staging/` | Temporary staging folder for daily update processing |
| `FaaSrLog/` | FaaSr workflow logs |

---

## 3. Logger Devices

The project currently uses 13 Zentra loggers:

```text
z6-19600
z6-12196
z6-19602
z6-19604
z6-19597
z6-19594
z6-19599
z6-12197
z6-19595
z6-19598
z6-12202
z6-19596
z6-19603
```

---

## 4. Final 12 Configuration CSVs

The 12 final configuration files are:

```text
C_O_N.csv
C_O_D.csv
C_O_F.csv
S_W_N.csv
S_C_N.csv
S_E_N.csv
S_W_D.csv
S_C_D.csv
S_E_D.csv
S_W_F.csv
S_C_F.csv
S_E_F.csv
```

Each code represents:

```text
<Location>_<Shade Zone>_<Irrigation>
```

Example:

```text
C_O_N = Control + Open + No irrigation
S_C_D = Solar Array + Center + Deficit irrigation
S_E_F = Solar Array + East + Full irrigation
```

---

## 5. Workflow Summary

### Workflow 1: Historical Parallel Backfill

This workflow downloads historical data from Zentra for all 13 loggers.

Typical workflow file:

```text
ZentraParallelBackfillPrivate.json
```

Main functions:

```text
initialize_parallel_backfill
backfill_zentra_history
```

Purpose:

```text
Fetch historical data from Zentra API
Save raw CSVs into zentra_raw_backfill/<serial>/
Maintain progress files for each logger
```

Important settings:

```text
start_date: 2024-04-04 00:00:00
chunk_days: 1
per_page: 2000
sleep_seconds: 65
```

This workflow is mainly for historical backfill and should not be used as the daily production workflow.

---

### Workflow 2: Strict Full Rebuild of 12 Config CSVs

This workflow rebuilds the 12 final Orchardgrass configuration CSVs from the raw historical data.

Typical workflow file:

```text
ZentraBuild12Configs_STRICT_FULL_REBUILD_S3PRIVATE_v2.json
```

Main Python file:

```text
05_build_12_config_csvs.py
```

Main functions:

```text
read_zentra_raw_files
form_12_config_csvs
upload_12_config_csvs
```

Purpose:

```text
Read raw data from zentra_raw_backfill/
Derive logger serial number from S3 path or filename
Use raw CSV port_num column
Match rows using logger_serial_number + port_num
Generate the 12 final CSVs
Upload them to zentra_final_12_configs/
```

Important arguments:

```text
rebuild_mode: full
upload_mode: overwrite
```

Use this workflow when the final 12 CSVs need to be rebuilt from scratch.

---

### Workflow 3: Daily Update With Anomaly Email Alerts

This is the main daily production workflow.

Typical workflow file:

```text
ZentraDailyUpdate_S3PRIVATE.json
```

Main Python files:

```text
07_daily_update_zentra.py
10_detect_anomalies_send_resend.py
05_build_12_config_csvs.py
```

Main functions:

```text
fetch_daily_zentra_raw
detect_daily_zentra_anomalies_and_send_email
read_zentra_raw_files
form_12_config_csvs
upload_12_config_csvs
```

Workflow order:

```text
FetchDailyZentraRaw
   ↓
DetectDailyZentraAnomaliesAndEmail
   ↓
ReadZentraRawFiles
   ↓
Form12ConfigCSVs
   ↓
Upload12ConfigCSVs
```

Purpose:

```text
Fetch latest daily data from Zentra
Save new raw files into zentra_raw_backfill/
Detect anomalous readings using measurement + value
Send one email alert using Resend if new anomalies are found
Merge new rows into the 12 final configuration CSVs
```

Important arguments:

```text
rebuild_mode: incremental
upload_mode: merge
```

This workflow does not rebuild all historical data. It only processes new daily data and merges it into the existing final CSVs.

---

### Workflow 4: Monthly Averages

This workflow creates monthly average CSVs from the final 12 configuration CSVs.

Typical workflow file:

```text
ZentraMonthlyAverages_S3PRIVATE.json
```

Main Python file:

```text
09_build_monthly_averages.py
```

Main functions:

```text
build_monthly_averages
finish_monthly_averages
```

Input:

```text
zentra_final_12_configs/*.csv
```

Output:

```text
Zentra_monthly_averages/<CONFIG>/<CONFIG>_monthly_averages.csv
Zentra_monthly_averages/<CONFIG>/<CONFIG>_monthly_summary.json
Zentra_monthly_averages/_monthly_build_summary.json
```

Requested monthly output columns:

```text
Location_code
Shade_zone_code
Irrigation_code
logger_name
logger_serial_number
port_num
port_description
date_time
Value
Units
```

Grouping logic:

```text
Location_code
Shade_zone_code
Irrigation_code
logger_name
logger_serial_number
port_num
port_description
Units
month
```

The `date_time` column is stored in `YYYY-MM-DD` format and represents the latest actual date available for that month and group.

---

### Workflow 5: Cleanup Temporary S3 Folders

This workflow removes temporary staging data after the daily and monthly workflows finish.

Typical workflow file:

```text
ZentraCleanupTemporaryS3_S3PRIVATE.json
```

Main Python file:

```text
08_cleanup_temporary_s3_folders.py
```

Main functions:

```text
cleanup_temporary_s3_folders
finish_cleanup_temporary_s3
```

Folders cleaned:

```text
zentra_phase2_staging/
zentra_phase2_daily_staging/
```

Protected folders:

```text
zentra_raw_backfill/
zentra_final_12_configs/
zentra_daily_update_state/
zentra_backfill_state/
zentra_anomaly_alerts/
Zentra_monthly_averages/
FaaSrLog/
```

---

## 6. Anomaly Detection and Resend Email Alerts

The anomaly detector uses only two columns:

```text
measurement
value
```

Thresholds:

| Measurement | Lower Threshold | Upper Threshold | Unit |
|---|---:|---:|---|
| Body Temperature | -5 | 40 | C |
| Target Temperature | -5 | 40 | C |
| Water Content | 0.150 | 0.500 | m3/m3 |
| Soil Temperature | -10 | 30 | C |

A value is flagged if:

```text
value < lower_threshold OR value > upper_threshold
```

Anomaly output folder:

```text
zentra_anomaly_alerts/
```

Files created:

```text
anomalies_latest.csv
anomalies_YYYYMMDDTHHMMSSZ.csv
new_anomalies_latest.csv
sent_anomaly_ids.json
anomaly_alert_summary_latest.json
anomaly_alert_summary_YYYYMMDDTHHMMSSZ.json
```

The workflow sends one summary email per daily run only if new anomalies are found.

To avoid duplicate email alerts, each anomaly receives a unique anomaly ID based on:

```text
logger_serial_number
port_num
datetime
timestamp_utc
measurement
value
units
source_file
```

Already emailed anomaly IDs are stored in:

```text
zentra_anomaly_alerts/sent_anomaly_ids.json
```

---

## 7. Required Secrets

The workflows require the following GitHub Actions / FaaSr secrets.

### Zentra API

```text
ZENTRA_TOKEN
```

### Resend Email API

```text
RESEND_API_KEY
ALERT_EMAIL_FROM
ALERT_EMAIL_TO
```

Example:

```text
ALERT_EMAIL_FROM = SmartTAP Alerts <alerts@yourdomain.com>
ALERT_EMAIL_TO = person1@oregonstate.edu,person2@oregonstate.edu
```

### Timer / GitHub Workflow Automation

```text
GH_PAT
```

---

## 8. Recommended Timers

### Daily Update

Runs the daily update and anomaly email workflow:

```text
17 8 * * *
```

This is approximately:

```text
1:17 AM Oregon time during PDT
```

### Monthly Averages

Runs three hours after the daily workflow:

```text
17 11 * * *
```

This is approximately:

```text
4:17 AM Oregon time during PDT
```

### Cleanup

Runs after daily and monthly workflows:

```text
17 13 * * *
```

This is approximately:

```text
6:17 AM Oregon time during PDT
```

Recommended order:

```text
Daily update + anomaly email
   ↓
Monthly averages
   ↓
Cleanup
```

---

## 9. Typical Implementation Steps

### Step 1: Update Python function repo

Add or update the Python files in:

```text
zentra_backfill_workflow_files/python/
```

Then commit:

```bash
git add python/
git commit -m "Update Zentra workflow functions"
git push
```

### Step 2: Update FaaSr workflow JSON repo

Add or update workflow JSON files in the FaaSr workflow repo.

Then commit:

```bash
git add .
git commit -m "Update Zentra FaaSr workflows"
git push
```

### Step 3: Register the workflow

In FaaSr:

```text
FAASR REGISTER
```

Choose the workflow JSON file.

### Step 4: Invoke the workflow

In FaaSr:

```text
FAASR INVOKE
```

Choose the same workflow JSON file.

---

## 10. Which Workflow Should Be Used When?

| Task | Workflow |
|---|---|
| Download all historical logger data | Historical Parallel Backfill |
| Rebuild all 12 final CSVs from scratch | Strict Full Rebuild |
| Daily production update | Daily Update With Anomaly Email |
| Send anomaly emails | Included in Daily Update With Anomaly Email |
| Build monthly average files | Monthly Averages |
| Clean temporary staging folders | Cleanup Temporary S3 Folders |

---

## 12. Current Production Flow

```text
1. ZentraDailyUpdate_S3PRIVATE.json
   - Fetches new daily raw data
   - Detects anomalies
   - Sends Resend email if needed
   - Updates final 12 config CSVs

2. ZentraMonthlyAverages_S3PRIVATE.json
   - Reads final 12 config CSVs
   - Builds monthly averages

3. ZentraCleanupTemporaryS3_S3PRIVATE.json
   - Deletes temporary staging folders
```

This is the recommended production setup.
