"""What /healthz reports (spec §12). With a Google key set, the weekly scan must be working: the CT 210 health check
alerts when the newest scan failed or none has completed in 9 days. Without a key, scanning is off and there is
nothing to report about it. A new install gets 9 days from the database's creation for its first scan.

Synced visits (Task 15): "visit sync failed" while a configured source's last run failed (visit_sync.failing). A
source that is switched off is never reported."""
import common
import settings
import visit_sync

SCAN_GRACE_DAYS = 9


def problems(con) -> list[str]:
    out = _scan_problems(con) if settings.GOOGLE_PLACES_KEY else []
    if visit_sync.failing(con):
        out.append("visit sync failed")
    return out


def _scan_problems(con) -> list[str]:
    out = []
    newest = con.execute("SELECT error FROM scan_run ORDER BY started_at DESC, id DESC LIMIT 1").fetchone()
    if newest is not None and newest["error"]:
        out.append("last scan failed")
    done = con.execute("SELECT MAX(finished_at) FROM scan_run WHERE finished_at IS NOT NULL AND error IS NULL").fetchone()[0]
    if done is None:
        created = con.execute("SELECT value FROM meta WHERE key = 'created_at'").fetchone()
        done = int(created[0]) if created else 0
    if common.now() - done > SCAN_GRACE_DAYS * 86400:
        out.append(f"no scan in {SCAN_GRACE_DAYS} days")
    return out
