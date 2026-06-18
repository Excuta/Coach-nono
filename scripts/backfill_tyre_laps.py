"""One-shot backfill: populate tyre_laps for all valid done laps missing tyre data."""
import sys
sys.path.insert(0, "/app")

from coach.db import conn
from coach.analysis.tyre_ops import compute_tyre_stats, store_tyre_stats
import pandas as pd

c = conn()
with c.cursor() as cur:
    cur.execute(
        "SELECT l.id, l.lap_path FROM laps l "
        "LEFT JOIN tyre_laps t ON t.lap_id = l.id "
        "WHERE l.status='done' AND l.valid=true AND t.lap_id IS NULL"
    )
    rows = cur.fetchall()

print(f"Backfilling {len(rows)} laps...")
ok = err = 0
for lap_id, lap_path in rows:
    try:
        df = pd.read_parquet(lap_path)
        stats = compute_tyre_stats(df)
        store_tyre_stats(c, lap_id, stats)
        ok += 1
    except Exception as e:
        print(f"  ERROR {lap_id[:8]}: {e}")
        err += 1

print(f"Done: {ok} ok, {err} errors")
