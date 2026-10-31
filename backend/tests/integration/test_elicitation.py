import uuid

from sqlalchemy import select

from app.persistence.models import Client, Meeting, OpenQuestion
from app.services import elicitation


class SameTopicEmbedder:
    """Questions sharing the first word are 'the same'; others orthogonal."""

    name = "t"
    dimensions = 384

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 384
            v[hash(t.split()[0].lower()) % 300] = 1.0
            out.append(v)
        return out


def _mid(s, cid):
    m = Meeting(client_id=cid, title="t", source="x")
    s.add(m)
    s.flush()
    return m.id


def test_answered_question_is_never_asked_again(factory, client_id):
    emb = SameTopicEmbedder()
    with factory() as s:
        mid = _mid(s, client_id)
        q1 = elicitation.add_question_if_new(s, emb, client_id, mid, "Audit scope?", "why", False)
        elicitation.next_question(s, client_id)
        elicitation.record_answer(s, client_id, q1.id)
        again = elicitation.add_question_if_new(
            s, emb, client_id, mid, "Audit retention too?", "w", False
        )
        assert again is None
        assert elicitation.next_question(s, client_id) is None


def test_one_at_a_time_blocking_first(factory, client_id):
    emb = SameTopicEmbedder()
    with factory() as s:
        mid = _mid(s, client_id)
        elicitation.add_question_if_new(s, emb, client_id, mid, "Alpha minor?", "w", False)
        b = elicitation.add_question_if_new(s, emb, client_id, mid, "Beta critical?", "w", True)
        first = elicitation.next_question(s, client_id)
        assert first.id == b.id and first.status == "asked"
        second = elicitation.next_question(s, client_id)
        assert second.question == "Alpha minor?"
        assert elicitation.next_question(s, client_id) is None


def test_duplicate_open_question_escalates_instead_of_repeating(factory, client_id):
    emb = SameTopicEmbedder()
    with factory() as s:
        mid = _mid(s, client_id)
        q = elicitation.add_question_if_new(s, emb, client_id, mid, "Audit scope?", "w", False)
        assert (
            elicitation.add_question_if_new(s, emb, client_id, mid, "Audit coverage?", "w", True)
            is None
        )
        assert q.blocking is True
        assert (
            len(list(s.scalars(select(OpenQuestion).where(OpenQuestion.client_id == client_id))))
            == 1
        )


def test_questions_do_not_leak_across_clients(factory, client_id):
    emb = SameTopicEmbedder()
    with factory() as s:
        other = Client(name=f"o-{uuid.uuid4().hex[:8]}")
        s.add(other)
        s.flush()
        elicitation.add_question_if_new(
            s, emb, other.id, _mid(s, other.id), "Audit scope?", "w", False
        )
        mine = elicitation.add_question_if_new(
            s, emb, client_id, _mid(s, client_id), "Audit scope?", "w", False
        )
        assert mine is not None  # other client's identical question must not suppress mine
        assert elicitation.next_question(s, client_id).id == mine.id
