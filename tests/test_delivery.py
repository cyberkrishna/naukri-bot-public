"""Unit tests for delivery.deliver_one — the advance/retry logic added after
the silent-failure bug. See commit de698a3."""
from datetime import datetime, timedelta, timezone

import pytest

from app import delivery
from app.models import JobPool, SentJob, User


def _make_user(s, *, chat_id=111, keywords="python", next_send_at=None):
    if next_send_at is None:
        next_send_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    u = User(
        chat_id=chat_id,
        username="t",
        keywords=keywords,
        exclude_keywords="",
        locations="",
        experience_years=0,
        require_zero_experience=True,
        frequency="daily",
        status="active",
        next_send_at=next_send_at,
    )
    s.add(u)
    return u


def _make_job(s, job_id="j1", title="python developer", query="python developer"):
    j = JobPool(
        job_id=job_id,
        title=title,
        company="Acme",
        location="Bangalore",
        experience="0-2 yrs",
        posted="Few hours ago",
        description="we love python",
        url="https://example.com/j",
        query=query,
        scraped_at=datetime.now(timezone.utc),
    )
    s.add(j)
    return j


def _set_send_outcomes(monkeypatch, outcomes):
    """outcomes: list of bools (one per _send_telegram call, in order). Last value repeats."""
    calls = {"i": 0}
    def fake_send(chat_id, html, dry_run=False):
        i = calls["i"]
        calls["i"] = i + 1
        return outcomes[min(i, len(outcomes) - 1)]
    monkeypatch.setattr(delivery, "_send_telegram", fake_send)
    return calls


def test_all_batches_succeed_writes_sent_jobs_and_advances(db_session, monkeypatch):
    with db_session() as s:
        _make_user(s)
        _make_job(s, "j1")
        _make_job(s, "j2")
        s.commit()
        original_nsa = s.get(User, 111).next_send_at

    _set_send_outcomes(monkeypatch, [True])  # header + body all OK

    sent, failed = delivery.deliver_one(111)

    assert sent == 2
    assert failed == 0
    with db_session() as s:
        u = s.get(User, 111)
        rows = s.query(SentJob).filter(SentJob.chat_id == 111).all()
        assert {r.job_id for r in rows} == {"j1", "j2"}
        assert u.last_send_at is not None
        assert u.next_send_at > original_nsa


def test_all_batches_fail_preserves_state(db_session, monkeypatch):
    with db_session() as s:
        _make_user(s)
        _make_job(s, "j1")
        s.commit()
        original_nsa = s.get(User, 111).next_send_at

    _set_send_outcomes(monkeypatch, [False])

    sent, failed = delivery.deliver_one(111)

    assert sent == 0
    assert failed == 1  # header failed → bail out, counts as one failed batch
    with db_session() as s:
        u = s.get(User, 111)
        assert s.query(SentJob).count() == 0
        assert u.last_send_at is None
        assert u.next_send_at == original_nsa  # NOT advanced — retry next tick


def test_partial_success_writes_only_delivered(db_session, monkeypatch):
    with db_session() as s:
        _make_user(s)
        # 11 matching jobs → batch size is 10, so 2 batches: 10 + 1
        for i in range(11):
            _make_job(s, job_id=f"j{i}", title=f"python developer {i}")
        s.commit()

    # header=True, batch1=True, batch2=False
    _set_send_outcomes(monkeypatch, [True, True, False])

    sent, failed = delivery.deliver_one(111)

    assert sent == 10  # only first batch's jobs counted as delivered
    assert failed == 1
    with db_session() as s:
        rows = s.query(SentJob).filter(SentJob.chat_id == 111).all()
        assert len(rows) == 10
        u = s.get(User, 111)
        assert u.last_send_at is not None  # at least one batch succeeded
        # next_send_at may come back tz-naive from sqlite; compare as naive UTC
        nsa = u.next_send_at.replace(tzinfo=None) if u.next_send_at.tzinfo is None else u.next_send_at
        now = datetime.now(timezone.utc).replace(tzinfo=None) if nsa.tzinfo is None else datetime.now(timezone.utc)
        assert nsa > now - timedelta(seconds=5)


def test_no_matching_jobs_advances_but_doesnt_set_last_send(db_session, monkeypatch):
    with db_session() as s:
        # keywords require "python" but no matching jobs exist
        _make_user(s, keywords="python")
        # Override default _make_job which mentions "python" in description
        j = JobPool(
            job_id="j1", title="java developer", company="Acme", location="Bangalore",
            experience="0-2 yrs", posted="Few hours ago", description="java spring boot",
            url="https://example.com/j", query="java developer",
            scraped_at=datetime.now(timezone.utc),
        )
        s.add(j)
        s.commit()
        original_nsa = s.get(User, 111).next_send_at

    # _send_telegram should not be called at all
    calls = _set_send_outcomes(monkeypatch, [False])  # would fail if called

    sent, failed = delivery.deliver_one(111)

    assert sent == 0
    assert failed == 0
    assert calls["i"] == 0  # confirms no send was attempted
    with db_session() as s:
        u = s.get(User, 111)
        assert u.last_send_at is None
        assert u.next_send_at > original_nsa  # advanced so we don't re-query on every tick
