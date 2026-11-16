import hashlib
import sqlite3

from hikari_bot.services.legacy_identity import LegacyRecords
from hikari_bot.services.platform import Identity
from hikari_bot.services.platform_state import PlatformState


def test_verified_link_reads_current_legacy_record_and_never_inherits_permissions(tmp_path):
    old = tmp_path / 'old.sqlite3'
    with sqlite3.connect(old) as db:
        db.executescript("CREATE TABLE mycard_bindings(qq_id TEXT, username TEXT);"
                         "CREATE TABLE mycard_subscriptions(username TEXT,target_type TEXT,target_id TEXT);"
                         "INSERT INTO mycard_bindings VALUES ('123','original');"
                         "INSERT INTO mycard_subscriptions VALUES ('watched','private','123');"
                         "INSERT INTO mycard_subscriptions VALUES ('unrelated','group','123');")
    digest = hashlib.sha256(old.read_bytes()).digest()
    state = PlatformState(tmp_path / 'new', legacy=LegacyRecords(old))
    actor = Identity('qq_official', 'app', 'user', 'opaque')
    other = Identity('qq_official', 'app', 'user', '123')
    assert state.binding(actor) is None
    state.bind(actor, 'temporary-copy')
    state.link_legacy(actor, '123', evidence='explicit owner request, verified C2C pairing')
    assert state.binding(actor) == 'original'
    assert state.binding(other) is None
    assert state.subscriptions(actor) == [('watched', 'paused_platform_unverified')]
    state.unsubscribe(actor, 'watched')
    assert state.subscriptions(actor) == []
    assert hashlib.sha256(old.read_bytes()).digest() == digest
    with sqlite3.connect(old) as db:
        db.execute("UPDATE mycard_bindings SET username='changed-in-original-service'")
    assert state.binding(actor) == 'changed-in-original-service'
    state.subscribe(actor, 'watched')
    assert state.subscriptions(actor) == [('watched', 'paused_platform_unverified')]
