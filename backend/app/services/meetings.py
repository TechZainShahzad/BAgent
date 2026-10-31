import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.transcript_file.parser import ParsedSegment, parse_transcript_file
from app.orchestration import jobs
from app.orchestration.audit import append_audit
from app.persistence.models import ConsentRecord, Meeting, TranscriptSegment
from app.services import conversation

ANALYZE_JOB = "meeting.analyze"


class ConsentRequiredError(Exception):
    """Capture/processing is refused without a recorded consent basis."""


def import_transcript(
    session: Session,
    client_id: uuid.UUID,
    path: Path,
    title: str,
    consent_basis: str,
    recorded_by: str,
    project_id: uuid.UUID | None = None,
) -> Meeting:
    """Parse a transcript file, persist segments + consent, enqueue analysis. All in one tx."""
    if not consent_basis.strip():
        raise ConsentRequiredError("a consent basis is required before importing a transcript")
    segments = parse_transcript_file(path)  # raises TranscriptParseError (visible)
    return import_segments(
        session,
        client_id,
        segments,
        title,
        consent_basis,
        recorded_by,
        project_id,
        "transcript_file",
    )


def import_segments(
    session: Session,
    client_id: uuid.UUID,
    segments: list[ParsedSegment],
    title: str,
    consent_basis: str,
    recorded_by: str,
    project_id: uuid.UUID | None = None,
    source: str = "transcript_file",
) -> Meeting:
    if not consent_basis.strip():
        raise ConsentRequiredError("a consent basis is required")
    meeting = Meeting(
        client_id=client_id,
        project_id=project_id,
        title=title,
        source=source,
        status="queued",
        consent_status="recorded",
    )
    session.add(meeting)
    session.flush()
    session.add(
        ConsentRecord(
            client_id=client_id, meeting_id=meeting.id, basis=consent_basis, recorded_by=recorded_by
        )
    )
    for s in segments:
        session.add(
            TranscriptSegment(
                client_id=client_id,
                meeting_id=meeting.id,
                idx=s.index,
                speaker=s.speaker,
                start_s=s.start_s,
                end_s=s.end_s,
                text=s.text,
            )
        )
        conversation.record_message(
            session, client_id, meeting.id, s.index, None, s.speaker, s.text, s.start_s
        )
    append_audit(
        session,
        "meeting.imported",
        {
            "meeting_id": str(meeting.id),
            "source": source,
            "segments": len(segments),
            "consent_recorded_by": recorded_by,
        },
        client_id,
    )
    jobs.enqueue(session, client_id, ANALYZE_JOB, str(meeting.id), {"meeting_id": str(meeting.id)})
    return meeting


def list_meetings(session: Session, client_id: uuid.UUID) -> list[Meeting]:
    return list(
        session.scalars(
            select(Meeting)
            .where(Meeting.client_id == client_id)
            .order_by(Meeting.created_at.desc())
        )
    )
