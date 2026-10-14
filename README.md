[![1 - Deploy Data Model (Flyway)](https://github.com/kleinnconrad/plsql-exponential-smoothing-implementation/actions/workflows/1-flyway-deploy.yml/badge.svg)](https://github.com/kleinnconrad/plsql-exponential-smoothing-implementation/actions/workflows/1-flyway-deploy.yml)  
[![2 - Deploy PL/pgSQL Procedure](https://github.com/kleinnconrad/plsql-exponential-smoothing-implementation/actions/workflows/2-plpgsql-deploy.yml/badge.svg)](https://github.com/kleinnconrad/plsql-exponential-smoothing-implementation/actions/workflows/2-plpgsql-deploy.yml)

## Table of Contents

* [PL/pgSQL Exponential Smoothing](#plpgsql-exponential-smoothing)
  * [Overview](#overview)
  * [Prerequisites](#prerequisites)
  * [Configuration](#configuration)
* [Database Infrastructure & CI/CD](#database-infrastructure--cicd)
  * [Why PostgreSQL (Supabase) instead of Oracle?](#why-postgresql-supabase-instead-of-oracle)
  * [The IPv6 GitHub Actions Issue & Solution](#the-ipv6-github-actions-issue--solution)
  * [Database Migrations with Flyway](#database-migrations-with-flyway)
  * [GitHub Actions Pipelines](#github-actions-pipelines)
  * [Required GitHub Secrets](#required-github-secrets)
* [Exponential Smoothing Formula](#exponential-smoothing-formula)
  * [The Formula](#the-formula)
  * [The Formula as Implemented](#the-formula-as-implemented)
  * [Curator's Notes (Known Quirks)](#curators-notes-known-quirks)
  * [Usage](#usage)


# PL/pgSQL Exponential Smoothing

This repository provides a handmade PL/pgSQL procedure for calculating simple exponential smoothing directly within a PostgreSQL Database (hosted on Supabase). It is meant for smoothing a time series and producing one-step-ahead forecasts, applying exponentially decreasing weights to older observations. Simple exponential smoothing models only the level of a series, not its trend, so on trending data the smoothed values lag behind the actuals.

The original, handmade **Oracle PL/SQL** procedure (`src/EXPONENTIAL_SMOOTHING.pls`) is kept as the museum piece; `src/exponential_smoothing_pg.pls` is its PL/pgSQL port. Both are preserved as-is, quirks included (see [Curator's Notes](#curators-notes-known-quirks)).

## Overview

The procedure `exponential_smoothing` processes sequential data and computes smoothed values using the expanded form of the exponential smoothing formula. It calculates 11 sliding observation windows and iteratively populates a target table with one smoothed value per window.

<img src="docs/exponential_smoothing_dark.png" width="900" alt="The 20 daily KUENDIGER sums as columns above an 11 x 20 grid with one row per loop pass i = 0..10. In each row a 10-date window, shaded by the weight 0.2 x 0.8^K, steps one column to the right, and an orange dot marks the anchor date, which moves from K = 11 down to K = 1. Bars on the right give each prog_1 value, from 31.900 down to 6.564.">

Each loop pass `i` sums the 10-date window `K BETWEEN 1 + i AND max_k - (10-i)` with the weight $\alpha(1-\alpha)^K$ and adds the anchor term $(1-\alpha)^{ANZ+1} \cdot Y_{11-i}$; the result `prog_1` is inserted into `analytics.var_x`. All numbers are computed from the dummy data in `sql/` by `scripts/viz.py` (`pip install -r scripts/requirements.txt`, then `python3 scripts/viz.py`).

## Prerequisites

* **PostgreSQL Database:** The script is written in PostgreSQL's native procedural language (PL/pgSQL).
* **Source Data Table:** A table containing the time-series data.  
* **Target Table:** A table to store the smoothed output variables.

## Configuration

Before deploying the procedure, you may need to adjust the following variables within the script to fit your specific dataset:

* `alpha`: The smoothing factor (default is `0.2`). A higher alpha discounts older observations faster. Here alpha is the weight on the newest observation, as in fpp3, pandas and statsmodels; Excel's Analysis ToolPak asks for the damping factor 1 − alpha instead. **Note on the data type:** in PostgreSQL, `NUMERIC` and `DECIMAL` are the same arbitrary-precision type, so `0.2` is stored exactly. In Oracle PL/SQL, `DECIMAL` and `NUMERIC` are subtypes of `NUMBER(38,0)` and round `0.2` to `0`, so a fractional alpha must be declared as plain `NUMBER` there.
* The script reads `analytics.source_table`, sums `KUENDIGER` per `DATUM` and orders the series by `DATUM`. Ensure your source table uses these names or update the script accordingly.
* The windowing is hardcoded around the number `10` (`FOR i IN 0..10`, window end `max_k - (10-i)`), so the source needs more than 10 distinct dates. With 10 or fewer, every window is empty and the procedure inserts `NULL`s.


# Database Infrastructure & CI/CD

This project uses an automated Infrastructure-as-Code (IaC) and Continuous Integration/Continuous Deployment (CI/CD) approach using GitHub Actions to manage the database schema and procedural logic.

## Why PostgreSQL (Supabase) instead of Oracle?

Initially, this project targeted the Oracle Cloud Always Free tier. However, due to notoriously strict and opaque automated account creation blocks on Oracle Cloud Infrastructure (OCI), securing a stable, permanent free database for CI/CD became unfeasible. 

**Supabase**, a fully managed open-source alternative built on top of **PostgreSQL**, was chosen as the replacement. PostgreSQL features its own procedural language (**PL/pgSQL**) that was closely modelled on the syntax and block structure of Oracle's PL/SQL. This allowed for an almost 1:1 translation of the mathematical logic while gaining a modern, developer-friendly cloud environment.

## The IPv6 GitHub Actions Issue & Solution

**The Problem:** Supabase provisions direct database connections using **IPv6** addresses. However, GitHub Actions virtual machines (`ubuntu-latest`) and standard Docker containers currently only support **IPv4** routing. This mismatch causes a `Network is unreachable` error when the pipeline attempts to connect to the database.

**The Solution:** To bridge this gap, we use the **Supabase Connection Pooler (Supavisor)**. 
1. The pooler provides an IPv4-compatible endpoint.
2. Because the pooler manages thousands of databases, the database username must be updated from `postgres` to include the specific tenant identifier (e.g., `postgres.[project-id]`).
3. The pooler host offers two modes: **session mode on port `5432`** and **transaction mode on port `6543`**. The examples below use port `6543` (transaction mode), which is enough for these small migrations and the `CREATE OR REPLACE` deployment. Should Flyway run into session-level problems (prepared statements, advisory locks), switch the URLs to port `5432` on the same pooler host to get session mode.

## Database Migrations with Flyway

**Flyway** is used to manage the structural data model (tables, schemas, and static seed data). 
* Migration scripts are located in the `sql/` directory and follow a strict naming convention (e.g., `V1__Initialize_analytics_schema.sql`, `V2__Insert_more_dummy_data.sql`).
* When Flyway runs, it checks an internal state table (`flyway_schema_history`) in the Supabase database. It compares the checksums of the files in the repository against what has already been executed, ensuring that new tables and data inserts are only applied once.
* Once a file is deployed via Flyway, it becomes immutable and should not be edited. Future structural changes require a new `V` script.

## GitHub Actions Pipelines

The CI/CD flow is intentionally split into two distinct pipelines to separate structural data model changes from procedural logic updates.

1. **Deploy Data Model (Flyway)** (`1-flyway-deploy.yml`)
   * **Trigger:** Manual (`workflow_dispatch`).
   * **Action:** Pulls the official Flyway Docker image, connects to Supabase via the IPv4 JDBC pooler URL, and applies any pending versioned SQL migrations from the `sql/` folder.
   * **Use Case:** Creating new tables, modifying schemas, or bulk inserting dummy historical data.

2. **Deploy PL/pgSQL Procedure** (`2-plpgsql-deploy.yml`)
   * **Trigger:** Automatic on `push` to the `main` branch, **only** if `src/exponential_smoothing_pg.pls` is modified. Can also be started manually (`workflow_dispatch`), e.g. to redeploy after the schema was recreated.
   * **Action:** Installs the native PostgreSQL client (`psql`) on the GitHub runner and executes the `.pls` file to compile the logic directly into the `analytics` schema. `psql` runs with `ON_ERROR_STOP=1`, so a compile error fails the pipeline instead of passing silently.
   * **Use Case:** Updating math formulas, fixing bugs in the procedure, or altering the smoothing window.

## Required GitHub Secrets

To run the pipelines successfully, the following secrets must be configured in **Settings > Secrets and variables > Actions**:

* `SUPABASE_DB_USER`: Your tenant-identified username (e.g., `postgres.[project-id]`).
* `SUPABASE_DB_PASSWORD`: Your Supabase database password.
* `SUPABASE_JDBC_URL`: The Flyway connection string using the IPv4 pooler (e.g., `jdbc:postgresql://aws-0-eu-west-1.pooler.supabase.com:6543/postgres`).
* `SUPABASE_PSQL_URL`: The standard connection URI for the `psql` client (e.g., `postgresql://postgres.[project-id]:[password]@aws-0-eu-west-1.pooler.supabase.com:6543/postgres`). Special characters in the password must be URL-encoded here.


# Exponential Smoothing Formula

The PL/pgSQL script implements the **expanded (or iterative) form** of Simple Exponential Smoothing (SES). Instead of computing the forecast recursively row-by-row ($S_t = \alpha Y_t + (1-\alpha)S_{t-1}$), the script calculates it using a set-based approach (one weighted `SUM` per window) and repeats it for 11 sliding windows.

## The Formula

Unrolling the recursion over a window of $n$ observations $Y_a, \dots, Y_b$ (with $Y_b$ the newest), started with $S_{a-1} = Y_a$, gives the textbook expanded form of forward SES:

$$S_b = \sum_{j=0}^{n-1} \alpha (1-\alpha)^j \cdot Y_{b-j} + (1-\alpha)^{n} \cdot Y_a$$

Where:
* **$S_b$**: The smoothed value at the end of the window, which is also the forecast for the next period.
* **$\alpha$** (`alpha`): The smoothing parameter (set to 0.2 in the script). It dictates how much weight is given to recent versus older observations.
* **$j$**: The age of an observation within the window (0 for the newest). For $0 < \alpha < 1$, the weights therefore decrease the older an observation is, which is the direction a forecast needs. Run backwards from the newest observation (backcasting, used to estimate a starting level), the same recursion makes the weights decrease toward the newest observation instead. Writing the parameter as a damping factor $1-\alpha$, as Excel does, changes how fast the weights decay, not in which direction.
* **$n$** (`ANZ`): The number of observations in the window.
* **$Y_a$**: The oldest value in the window, acting as the initial anchor $S_0$.

All weights add up to exactly 1.

## The Formula as Implemented

What the antique script actually computes for each loop index $i = 0, \dots, 10$ is:

$$S_i = \sum_{K=1+i}^{N-10+i} \alpha (1-\alpha)^K \cdot Y_K + (1-\alpha)^{n+1} \cdot Y_{11-i}$$

Where:
* **$S_i$** (`prog_1`): The value inserted into `analytics.var_x` for iteration $i$.
* **$Y_K$** (`KUENDIGER`): The sum of the observations for one date.
* **$K$** (`K`): The **absolute** row number of the date in the whole series, ascending by `DATUM` (oldest = 1). It is neither the age of the observation nor relative to the window.
* **$N$** (`max_k`): The number of distinct dates in the series.
* **$n$** (`ANZ`): The number of observations in the window, always $N - 10$.
* **$Y_{11-i}$**: The anchor value. It is selected with `ORDER BY DATUM DESC` and `rn = max_k - (10-i)`, which resolves to the $(11-i)$-th oldest date.

## Curator's Notes (Known Quirks)

Both procedures are kept as authentic exhibits, which includes their bugs. Running the PL/pgSQL port against the bundled dummy data (20 dates, values between 30 and 60) next to textbook SES over the same windows shows the effect:

| $i$ | Window ($K$) | `prog_1` from the port | Textbook SES, same window | Sum of weights in the port |
|---:|:---:|---:|---:|---:|
| 0 | 1..10 | 31.900 | 42.784 | 0.800 |
| 1 | 2..11 | 27.581 | 45.257 | 0.657 |
| 2 | 3..12 | 24.020 | 46.387 | 0.543 |
| 3 | 4..13 | 19.693 | 46.158 | 0.452 |
| 4 | 5..14 | 16.376 | 48.248 | 0.378 |
| 5 | 6..15 | 14.313 | 49.106 | 0.320 |
| 6 | 7..16 | 12.352 | 50.563 | 0.273 |
| 7 | 8..17 | 10.728 | 53.309 | 0.236 |
| 8 | 9..18 | 9.947 | 53.769 | 0.206 |
| 9 | 10..19 | 7.807 | 53.001 | 0.182 |
| 10 | 11..20 | 6.564 | 54.338 | 0.163 |

1. **Weights decline toward the newest observation.** The exponent is the ascending row number `k`, so within a window each weight is $1-\alpha$ times the previous one, from the *oldest* observation to the newest (0.160, 0.128, …, 0.021 in the first window). Forward SES, which a one-step-ahead forecast such as `prog_1` needs, does the opposite and gives the newest observation the most weight. Exponential smoothing run backwards (backcasting) does give the oldest observation the most weight, but it estimates the level at the *start* of the window rather than a forecast, and the procedure is not a correct backward smoothing either (see 2 and 3). The direction is set by what the exponent counts, not by how alpha is defined: reading 0.2 as a damping factor ($\alpha = 0.8$) only makes the weights shrink faster (0.160, 0.032, 0.006, …). The weights grow toward the newest observation only for $\alpha < 0$ or $\alpha > 2$, and then they are negative or alternate in sign, so the result is no longer an average.
2. **Weights are not re-based per window.** Because `k` is absolute, every step of the sliding window raises the exponent of every observation in the window by one. The weights sum to 0.80 (= $1-\alpha$) in the first window and to 0.16 in the last, so the output sinks towards zero instead of following the data. This is the decisive defect, and it holds whatever the direction or the alpha convention: forward and backward SES, with alpha as the weight or as the damping factor, all have weights that add up to 1 and return a constant series unchanged. The port turns a constant series of 50 into 40.0, 32.9, 27.1, …, 8.1. Only $\alpha = 0$ makes every window's weights add up to 1, and then nothing is smoothed (see 4).
3. **Wandering anchor.** The anchor is picked by *descending* row number and lands on $K = 11 - i$, whatever the length of the series. It walks backwards from $K = 11$ to $K = 1$ while the window walks forward, which fits neither direction of smoothing. Forward SES starts from the oldest value in the window ($K = 1 + i$), which the code hits only at $i = 5$. A backcast starts from the newest end of the window, which the code hits only in the first window of the 20-date dummy data; that is why that one row equals exactly $(1-\alpha)$ times a backcast. The exponent `ANZ + 1` is also one too high once the other exponents count the ages $0, \dots, n-1$. Changing it alone does not fix the sum, because `k` starting at 1 already gives every weight an extra factor of $1-\alpha$.
4. **Oracle `DECIMAL` rounding.** In the Oracle original, `alpha decimal := 0.2` is rounded to `0`, because PL/SQL defines `DECIMAL` (like `NUMERIC`) as a subtype of `NUMBER(38,0)`. With alpha at 0, every weight in the sum is 0 and the anchor weight is 1, so the Oracle procedure just copies the first 11 raw values in reverse order (53, 48, 50, …, 30 for the dummy data). The port does not have this problem, since an unconstrained `NUMERIC` keeps its decimals in PostgreSQL.
5. **Ever-growing IDs.** `TRUNCATE` without `RESTART IDENTITY` keeps the identity sequence of `var_x`, so the second run writes IDs 12–22, the third 23–33, and so on.
6. **No date in the output.** `var_x` stores only `id` and `prog_1`, so the window a value belongs to can only be inferred from the insert order.
7. **Smaller curiosities.** `PARTITION BY 1` (one partition, same as leaving it out), the declared `i` that the `FOR` loop shadows with its own variable, `ORDER BY` inside inline views (Oracle), dynamic SQL for a static `TRUNCATE`, the series being re-aggregated twice per iteration plus once up front (23 aggregations per call), and the comment "Postgres requires aliases for subqueries", which only holds for PostgreSQL 15 and older.

A restoration in the antique style needs only four changes in the port: use the age `(max_k - (10-i)) - k` as the exponent, use `ANZ` as the anchor exponent, and pick the anchor with `ORDER BY DATUM ASC` and `rn = 1 + i`. With those changes the procedure reproduces the textbook column of the table above exactly.

## Usage

1. The procedure is automatically compiled into the `analytics` schema via GitHub Actions.
2. To execute the calculation and generate the smoothed data window, run the following command in the Supabase SQL Editor:
   ```sql
   CALL analytics.exponential_smoothing();
   ```
3. Read the 11 results (one per window, oldest window first) with:
   ```sql
   SELECT id, prog_1 FROM analytics.var_x ORDER BY id;
   ```
