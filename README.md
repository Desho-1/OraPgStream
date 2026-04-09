# 🚀 OraPgStream

**OraPgStream** is a lightweight, agentless Change Data Capture (CDC) tool that streams data changes from **Oracle** to **PostgreSQL** in near real-time using LogMiner.

Built for **simplicity**, **control**, and **speed**, OraPgStream runs entirely in your environment — no external services, no complex infrastructure.

---

## ✨ Key Features

- ⚡ Fast to install — up and running in minutes  
- 🔄 real-time CDC using Oracle LogMiner  
- 🧠 SCN-based tracking for reliable replay  
- 🔒 Fully local — no external connections  
- 🛠️ Simple and transparent architecture  
- 🔁 Automatic resume from last processed state 
- 📊 Applies changes in **batches** for better performance
- 📦 Lightweight (Python-based, minimal dependencies)


## 🧩 How It Works
Oracle (Redo Logs / LogMiner)
-->
OraPgStream (CDC Engine)
-->
PostgreSQL (Apply Engine)

## 📁 Project Structure
```sql
 ora2pg-cdc/
  ├── cdc_engine/      # OraPgStream engine
  ├── orchestrator/    # Workflow control (future use)
  ├── state/           # SCN tracking (SQLite)
  ├── config/          # Configuration files
```
---

## 📦 Prerequisites

Make sure the following are installed:

- Python 3.8+
- Oracle Client
- PostgreSQL instance (accessible)

### Required Python packages:
- `oracledb`
- `psycopg2`
- `pyyaml`


## ⚠️ Important: Enable LogMiner (Oracle)

Before running OraPgStream, LogMiner must be properly configured.

## 📝 Notes
These steps are mandatory for accurate CDC

💡 For better performance under load, consider increasing:

- PGA (pga_aggregate_target, pga_aggregate_limit)
- Shared Pool (shared_pool_size)

*(Tune based on your system capacity and workload)*

- Run the following as a privileged user:

```sql
-- Enable supplemental logging (required for CDC)
ALTER DATABASE ADD SUPPLEMENTAL LOG DATA;
ALTER DATABASE ADD SUPPLEMENTAL LOG DATA (ALL) COLUMNS;

-- Build LogMiner dictionary in redo logs
EXEC DBMS_LOGMNR_D.BUILD(
  OPTIONS => DBMS_LOGMNR_D.STORE_IN_REDO_LOGS
);
```

---

# ▶️ Getting Started

- download OraPgStream.tar.gz, PgApply.tar.gz & install.sh
- bash install.sh
- source venv/bin/activate
- run `./OraPgStream`
### ⚠️ Note: OraPgStream runs continuously as a live replication tool
To stop replication safely, press `Ctrl+C`.

## 🔍 What OraPgStream Does
- Reads changes from Oracle using LogMiner
- Tracks progress using SCN (System Change Number)
- Converts Oracle redo into executable SQL
- Applies changes to PostgreSQL in batches
- Logs all operations for full visibility

## 🔒 Security & Transparency
- ✅ Runs entirely locally
- ✅ No external API calls
- ✅ No data leaves your environment
- ✅ Full logging and traceability

