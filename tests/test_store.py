from daybreak.store import Store, SettingsFiles
from daybreak.model import Settings
import pytest

def test_settings_and_secret_are_separate(tmp_path):
    f=SettingsFiles(tmp_path); s=Settings(ha_url='http://home.test:8123')
    f.save(s,'secret-do-not-copy'); assert f.load().ha_url==s.ha_url
    assert f.token()=='secret-do-not-copy'
    assert 'secret-do-not-copy' not in (tmp_path/'settings.json').read_text()
    assert (tmp_path/'credentials').stat().st_mode&0o777==0o600

def test_invalid_settings_do_not_replace(tmp_path):
    f=SettingsFiles(tmp_path); f.save(Settings(),'keep')
    bad=Settings(lead_seconds=0)
    with pytest.raises(ValueError): f.save(bad)
    assert f.load().lead_seconds==10 and f.token()=='keep'

def test_backup_contains_no_token(tmp_path):
    f=SettingsFiles(tmp_path); f.save(Settings(),'private'); name=f.backup()
    assert 'private' not in (tmp_path/'backups'/name).read_text()
    s=f.load(); s.loud_alarm=5; f.save(s); f.restore(name)
    assert f.load().loud_alarm==7 and f.token()=='private'
    with pytest.raises(ValueError): f.restore('../credentials')

def test_completion_survives_reopen(tmp_path):
    p=tmp_path/'state.db'; db=Store(p); db.ensure_session('wake:day',{'start':'s','end':'e','restore':'r'},'real')
    db.complete('wake:day','QR'); db.close(); db=Store(p)
    assert db.session('wake:day')['completed']==1
    db.close()

def test_event_history_bounded(tmp_path):
    db=Store(tmp_path/'state.db')
    for i in range(250): db.event('INFO',f'event {i}')
    assert len(db.events(10))==10
    assert db.events(1)[0]['message']=='event 249'

def test_override_persistence(tmp_path):
    db=Store(tmp_path/'state.db'); db.set_meta('override',{'date':'2026-09-17','start':'07:10'})
    assert db.meta('override')['start']=='07:10'
