# Database Migrations (Flyway)

This directory contains the structural database migration scripts managed by **Flyway**, an open-source database schema migration tool. It allows us to treat our database infrastructure as code, ensuring that schema changes and seed data insertions are version-controlled, reproducible, and safely deployed.

## How We Use Flyway in This Repository

Flyway tracks the state of the database by calculating checksums of these SQL files and recording their execution in a dedicated internal table (`flyway_schema_history`). 

### 1. File Naming Convention
Flyway relies on a strict naming convention to determine the execution order. Every file added to this directory must follow this structure:

`V<Version>__<Description>.sql`
*(Note the double underscore `__` between the version and the description)*

**Examples:**
* `V1__Initialize_analytics_schema.sql`
* `V2__Insert_more_dummy_data.sql`

### 2. The Golden Rule: Immutability
Once a migration script has been successfully deployed to the database, **it must never be edited**. 

If you edit a previously executed file, Flyway's checksum validation will fail, and the pipeline will crash. If you need to add a new column, alter a table, or insert new data, you must create a **new** versioned file (e.g., `V3__Add_new_column.sql`).

### 3. Scope
Place only structural data model changes (DDL) and static seed data (DML) in this folder. Procedural logic (like PL/pgSQL functions and procedures) is managed separately in the `/src` directory to allow for continuous `CREATE OR REPLACE` deployments.

---

## How Flyway is Invoked in the Pipeline

To prevent accidental structural modifications from triggering automatically on a code commit, the Flyway deployment is strictly separated into a **manual** GitHub Actions workflow: `1 - Deploy Data Model (Flyway)`.

### The Execution Flow

1. **Manual Trigger:** A developer navigates to the GitHub Actions tab, selects the workflow, and manually clicks "Run workflow".
2. **Ephemeral Runner:** GitHub spins up a temporary Ubuntu virtual machine.
3. **Docker Execution:** The pipeline pulls the official Flyway Docker image (`flyway/flyway:10`) and executes it natively. It mounts this `/sql` directory directly into the container so Flyway can scan for new scripts.

Under the hood, the GitHub Action executes the following command:

```bash
docker run --rm \
  -v ${{ github.workspace }}/sql:/flyway/sql \
  flyway/flyway:10 \
  -url="${{ secrets.SUPABASE_JDBC_URL }}" \
  -user="${{ secrets.SUPABASE_DB_USER }}" \
  -password="${{ secrets.SUPABASE_DB_PASSWORD }}" \
  migrate
```

No `-locations` flag is needed: the Flyway image looks for migrations in `/flyway/sql` by default, which is exactly where this directory is mounted.
