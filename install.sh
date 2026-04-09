#!/bin/bash

echo "🚀 Starting CDC Tool Installer..."
echo "================================="

# -------------------------------
# Helper: ask yes/no
# -------------------------------
ask_install() {
    read -p "👉 $1 is missing. Install it? (y/n): " choice
    case "$choice" in
        y|Y ) return 0 ;;
        * ) return 1 ;;
    esac
}

# -------------------------------
# Check command exists
# -------------------------------
check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

# -------------------------------
# 1️⃣ SYSTEM PACKAGES
# -------------------------------
echo "🔍 Checking system packages..."

PACKAGES=("python3" "git" "gcc")

for pkg in "${PACKAGES[@]}"; do
    if ! check_cmd "$pkg"; then
        echo "❌ $pkg not found"
        if ask_install "$pkg"; then
            sudo apt update
            sudo apt install -y $pkg
        else
            echo "⚠ Skipping $pkg"
        fi
    else
        echo "✅ $pkg found"
    fi
done

# -------------------------------
# 2️⃣ PYTHON VENV
# -------------------------------
if ! check_cmd "python3"; then
    echo "❌ Python3 required. Exiting."
    exit 1
fi

echo "🐍 Setting up virtual environment..."

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate

pip install --upgrade pip
pip install oracledb psycopg2-binary pyyaml sqlparse

echo "✅ Python environment ready"

# -------------------------------
# 3️⃣ ORACLE CLIENT
# -------------------------------
echo "🔍 Checking Oracle Instant Client..."

DEFAULT_ORACLE_PATH="/opt/oracle/instantclient_23_26"

if [ -d "$DEFAULT_ORACLE_PATH" ]; then
    ORACLE_PATH=$DEFAULT_ORACLE_PATH
    echo "✅ Found Oracle client at $ORACLE_PATH"
else
    echo "❌ Oracle client not found"

    read -p "👉 Enter Oracle Instant Client path: " ORACLE_PATH

    if [ ! -d "$ORACLE_PATH" ]; then
        echo "❌ Invalid path. Exiting."
        exit 1
    fi
fi

# Export environment
export LD_LIBRARY_PATH=$ORACLE_PATH
export PATH=$PATH:$ORACLE_PATH

echo "✅ Oracle environment configured"

# -------------------------------
# 4️⃣ CREATE PROJECT STRUCTURE
# -------------------------------
echo "📁 Creating project structure..."

mkdir -p ~/ora2pg-cdc/{orchestrator,cdc_engine,state,config}
tar -xzvf OraPgStream.tar.gz
tar -xzvf PgApply.tar.gz
mv OraPgStream pg_apply ~/ora2pg-cdc/cdc_engine/
mv main.py ~/ora2pg-cdc/orchestrator/ 
cd ~/ora2pg-cdc

# -------------------------------
# 5️⃣ SQLITE INIT
# -------------------------------
echo "🗄 Initializing SQLite..."

sqlite3 state/state.db <<EOF
CREATE TABLE IF NOT EXISTS scn_state (last_scn NUMBER);
INSERT INTO scn_state SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM scn_state);
EOF

echo "✅ SQLite ready"

# -------------------------------
# 6️⃣ ORACLE CONNECTION
# -------------------------------
echo ""
echo "🔗 Oracle Connection Setup"
echo "--------------------------"

read -p "Oracle Host: " ORA_HOST
read -p "Oracle Port (default 1521): " ORA_PORT
ORA_PORT=${ORA_PORT:-1521}

read -p "Oracle Service/SID: " ORA_SERVICE
read -p "Oracle User: " ORA_USER
read -s -p "Oracle Password: " ORA_PASS
echo ""

# -------------------------------
# 7️⃣ POSTGRES CONNECTION
# -------------------------------
echo ""
echo "🐘 PostgreSQL Connection Setup"
echo "------------------------------"

read -p "Postgres Host: " PG_HOST
read -p "Postgres Port (default 5432): " PG_PORT
PG_PORT=${PG_PORT:-5432}

read -p "Database: " PG_DB
read -p "User: " PG_USER
read -s -p "Password: " PG_PASS
echo ""

# -------------------------------
# 8️⃣ SAVE CONFIG
# -------------------------------
echo "💾 Saving configuration..."

cat > config/config.yaml <<EOF
oracle:
  host: "$ORA_HOST"
  port: $ORA_PORT
  service: "$ORA_SERVICE"
  user: "$ORA_USER"
  password: "$ORA_PASS"

postgres:
  host: "$PG_HOST"
  port: $PG_PORT
  database: "$PG_DB"
  user: "$PG_USER"
  password: "$PG_PASS"

oracle_client_path: "$ORACLE_PATH"
EOF

echo "✅ Config saved at config/config.yaml"

# -------------------------------
# 9️⃣ TEST CONNECTIONS
# -------------------------------
echo "🧪 Testing connections..."

python <<EOF
import oracledb
import psycopg2
import sys

oracle_ok = False
pg_ok = False

print("🔌 Testing Oracle connection...")

try:
    conn = oracledb.connect(
        user="$ORA_USER",
        password="$ORA_PASS",
        dsn="$ORA_HOST:$ORA_PORT/$ORA_SERVICE"
    )
    print("✅ Oracle connection successful")
    conn.close()
    oracle_ok = True
except Exception as e:
    print("❌ Oracle connection failed")
    print("👉 Possible causes:")
    print("   - Wrong credentials")
    print("   - Oracle service not reachable")
    print("   - Network issue (check host/port)")
    print(f"   Error: {e}")

print("\n🔌 Testing PostgreSQL connection...")

try:
    conn = psycopg2.connect(
        host="$PG_HOST",
        port=$PG_PORT,
        dbname="$PG_DB",
        user="$PG_USER",
        password="$PG_PASS"
    )
    print("✅ PostgreSQL connection successful")
    conn.close()
    pg_ok = True
except Exception as e:
    print("❌ PostgreSQL connection failed")
    print("👉 Possible causes:")
    print("   - Wrong credentials")
    print("   - PostgreSQL not reachable")
    print("   - Network issue (check host/port)")
    print(f"   Error: {e}")

# Final status
print("\n=================================")

if oracle_ok and pg_ok:
    print("🎉 Step completed successfully!")
    sys.exit(0)
else:
    print("⚠️ Setup completed with errors.")
    print("👉 Please fix the connection issues and re-run the installer.")
    sys.exit(1)

EOF

# -------------------------------
# DONE
# -------------------------------
echo ""
echo "================================="
echo "Next step:"
echo "👉 Activate env: source venv/bin/activate"
echo "👉 Run your CDC tool"
