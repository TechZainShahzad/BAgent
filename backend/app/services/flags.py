"""Runtime control flags: kill switch and autonomy mode. DB value wins over env default."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import Settings
from app.orchestration.audit import append_audit
from app.orchestration.policy import Mode, stricter_mode
from app.persistence.models import Project, SystemFlag


def _get(session: Session, key: str) -> str | None:
    flag = session.get(SystemFlag, key)
    return flag.value if flag else None


def _set(session: Session, key: str, value: str, by: str) -> None:
    flag = session.get(SystemFlag, key)
    if flag is None:
        session.add(SystemFlag(key=key, value=value, updated_by=by, updated_at=datetime.now(UTC)))
    else:
        flag.value, flag.updated_by, flag.updated_at = value, by, datetime.now(UTC)


def kill_switch_on(session: Session, settings: Settings) -> bool:
    v = _get(session, "kill_switch")
    return settings.kill_switch if v is None else v == "on"


def set_kill_switch(session: Session, on: bool, by: str) -> None:
    _set(session, "kill_switch", "on" if on else "off", by)
    append_audit(session, "control.kill_switch", {"on": on, "by": by})


def autonomy_mode(session: Session, settings: Settings, project: Project | None = None) -> Mode:
    """Stricter of: env/global, DB override, project setting."""
    mode = stricter_mode(settings.autonomy_mode, _get(session, "autonomy_mode"))
    return stricter_mode(mode.value, project.autonomy_mode if project else None)


def set_autonomy_mode(session: Session, mode: Mode, by: str) -> None:
    _set(session, "autonomy_mode", mode.value, by)
    append_audit(session, "control.autonomy_mode", {"mode": mode.value, "by": by})


def purge_due(session: Session, now: datetime | None = None, every_s: float = 3600.0) -> bool:
    """True (and records the time) when the scheduled retention purge should run."""
    t = now or datetime.now(UTC)
    last = _get(session, "last_purge_at")
    if last is not None and (t - datetime.fromisoformat(last)).total_seconds() < every_s:
        return False
    _set(session, "last_purge_at", t.isoformat(), "scheduler")
    return True
