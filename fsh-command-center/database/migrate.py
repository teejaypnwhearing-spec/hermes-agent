import os
import sys
import psycopg2
from pathlib import Path

def migrate():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not set")
        sys.exit(1)

    migrations_dir = Path(__file__).parent / "migrations"
    if not migrations_dir.exists():
        print(f"Error: Migrations directory not found at {migrations_dir}")
        sys.exit(1)

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()

    # Create schema_versions table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Get applied migrations
    cur.execute("SELECT version FROM schema_versions")
    applied = {row[0] for row in cur.fetchall()}

    # Find and apply new migrations
    migration_files = sorted(migrations_dir.glob("V*__*.sql"))
    for file in migration_files:
        version = file.name.split("__")[0]
        if version not in applied:
            print(f"Applying migration: {file.name}")
            with open(file, "r") as f:
                sql = f.read()
                try:
                    cur.execute(sql)
                    cur.execute("INSERT INTO schema_versions (version) VALUES (%s)", (version,))
                    print(f"Successfully applied {version}")
                except Exception as e:
                    print(f"Error applying {version}: {e}")
                    conn.rollback()
                    sys.exit(1)
        else:
            print(f"Skipping already applied migration: {file.name}")

    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
