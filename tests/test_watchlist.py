"""
tests/test_watchlist.py — CineLog

Tests for the watchlist service.
Follows the same fixture and assertion structure as tests/test_collection.py.
"""

import pytest
from app import create_app, db
from models import User, Film, WatchlistEntry
from services.watchlist_service import (
    add_to_watchlist,
    remove_from_watchlist,
    get_watchlist,
    AlreadyOnWatchlistError,
    NotOnWatchlistError,
)
from services.collection_service import FilmNotFoundError


@pytest.fixture
def app():
    """Create an isolated test app with an in-memory database."""
    app = create_app(config={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def sample_user(app):
    """A user to use in tests."""
    with app.app_context():
        user = User(username="testuser", email="test@example.com")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def sample_film(app):
    """A film to use in tests."""
    with app.app_context():
        film = Film(title="Paddington 2", year=2017, genre="Comedy")
        db.session.add(film)
        db.session.commit()
        return film.id


# ── Deduplication ────────────────────────────────────────────────────────────

def test_add_to_watchlist_duplicate_raises(app, sample_user, sample_film):
    """
    Adding the same film twice should raise AlreadyOnWatchlistError,
    not silently create a duplicate entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        with pytest.raises(AlreadyOnWatchlistError):
            add_to_watchlist(user_id=sample_user, film_id=sample_film)

        # Confirm only one entry exists
        count = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).count()
        assert count == 1


def test_add_to_watchlist_same_film_different_users(app, sample_user, sample_film):
    """
    Deduplication is per user. A second user adding a film that's already on
    someone else's watchlist should succeed, not raise AlreadyOnWatchlistError.
    """
    with app.app_context():
        other_user = User(username="otheruser", email="other@example.com")
        db.session.add(other_user)
        db.session.commit()

        add_to_watchlist(user_id=sample_user, film_id=sample_film)
        add_to_watchlist(user_id=other_user.id, film_id=sample_film)

        count = WatchlistEntry.query.filter_by(film_id=sample_film).count()
        assert count == 2


# ── Nonexistent film ─────────────────────────────────────────────────────────

def test_add_to_watchlist_nonexistent_film_raises(app, sample_user):
    """
    Adding a film_id that doesn't exist in the database should raise
    FilmNotFoundError, not a database integrity error.
    """
    with app.app_context():
        fake_film_id = "00000000-0000-0000-0000-000000000000"

        with pytest.raises(FilmNotFoundError):
            add_to_watchlist(user_id=sample_user, film_id=fake_film_id)

        # Nothing should have been written
        assert WatchlistEntry.query.count() == 0


# ── Visibility ───────────────────────────────────────────────────────────────

def test_add_to_watchlist_defaults_to_private(app, sample_user, sample_film):
    """
    Entries are private unless the caller says otherwise.
    """
    with app.app_context():
        entry = add_to_watchlist(user_id=sample_user, film_id=sample_film)

        assert entry.public is False


def test_add_to_watchlist_public(app, sample_user, sample_film):
    """
    Passing public=True should store an entry other users can see.
    """
    with app.app_context():
        entry = add_to_watchlist(
            user_id=sample_user, film_id=sample_film, public=True
        )

        in_db = db.session.get(WatchlistEntry, entry.id)
        assert in_db.public is True


def test_add_endpoint_rejects_non_boolean_public(app, sample_user, sample_film):
    """
    The string "false" is truthy in Python, so the endpoint should reject it
    instead of quietly saving a public entry the user meant to keep private.
    """
    client = app.test_client()
    response = client.post(
        f"/watchlist/{sample_user}/add",
        json={"film_id": sample_film, "public": "false"},
    )

    assert response.status_code == 400
    with app.app_context():
        assert WatchlistEntry.query.count() == 0


# ── Remove ───────────────────────────────────────────────────────────────────

def test_remove_from_watchlist_deletes_entry(app, sample_user, sample_film):
    """
    Removing a film that's on the watchlist should delete the entry.
    """
    with app.app_context():
        add_to_watchlist(user_id=sample_user, film_id=sample_film)

        result = remove_from_watchlist(user_id=sample_user, film_id=sample_film)

        assert result is True
        in_db = WatchlistEntry.query.filter_by(
            user_id=sample_user, film_id=sample_film
        ).first()
        assert in_db is None


def test_remove_from_watchlist_not_on_watchlist_raises(app, sample_user, sample_film):
    """
    Removing a film that was never added should raise NotOnWatchlistError.
    """
    with app.app_context():
        with pytest.raises(NotOnWatchlistError):
            remove_from_watchlist(user_id=sample_user, film_id=sample_film)


# ── get_watchlist sort order ─────────────────────────────────────────────────

def test_get_watchlist_returns_newest_first(app, sample_user):
    """
    get_watchlist() should return films sorted by date_added descending
    (most recently added first), matching get_collection().
    """
    with app.app_context():
        from datetime import datetime, timezone, timedelta

        # Titles chosen so alphabetical order would give the opposite result
        film_a = Film(title="Alien", year=1979, genre="Horror")
        film_b = Film(title="Blade Runner", year=1982, genre="Sci-Fi")
        db.session.add_all([film_a, film_b])
        db.session.commit()

        earlier = datetime.now(timezone.utc) - timedelta(days=5)
        later = datetime.now(timezone.utc)

        entry_a = WatchlistEntry(user_id=sample_user, film_id=film_a.id, date_added=earlier)
        entry_b = WatchlistEntry(user_id=sample_user, film_id=film_b.id, date_added=later)
        db.session.add_all([entry_a, entry_b])
        db.session.commit()

        watchlist = get_watchlist(sample_user)
        titles = [f["title"] for f in watchlist]

        # Blade Runner was added later, so it should come first
        assert titles == ["Blade Runner", "Alien"]
