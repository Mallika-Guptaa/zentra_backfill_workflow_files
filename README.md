# Zentra Backfill Workflow Files

This repository contains the current development work for building an automated FaaSr-based data pipeline for retrieving Zentra sensor data, storing raw data in S3-compatible cloud storage, and preparing structured datasets for the Orchardgrass / NEWAg experiment.

The main goal is to move from manual data retrieval to a reproducible cloud workflow that can:

1. Fetch data from the Zentra Cloud API.
2. Store raw logger-level CSV files in S3 / Backblaze.
3. Backfill historical data from the beginning of the experiment.
4. Format the raw logger data into 12 final treatment/configuration CSVs.
5. Support future automation, dashboards, and daily/periodic updates.

---

## Project Context

The Zentra database contains sensor readings from multiple NEWAg loggers. The experiment is organized into 12 treatment/configuration groups based on:

- Location
- Shade Zone
- Irrigation treatment
- Logger name
- Logger serial number
- Port number
- Sensor description

The final expected output is a set of 12 CSV files, where each CSV corresponds to one experimental configuration.

Example:

```text
C_O_N.csv = Control + Open + No irrigation
S_W_D.csv = Solar Array + West + Deficit irrigation
S_E_F.csv = Solar Array + East + Full irrigation
