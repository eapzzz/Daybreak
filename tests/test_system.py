import json
import pytest
from daybreak.system import scrub,export_report,service_action

def test_redact_credentials_in_command_output():
    assert 'password' not in scrub('https://name:password@example.test/repo')

def test_report_file_private_and_no_overwrite(tmp_path):
    p=tmp_path/'report.json'; export_report({'network':'Home'},str(p))
    assert p.stat().st_mode&0o777==0o600
    with pytest.raises(ValueError): export_report({},str(p))

async def test_system_action_is_allowlisted():
    with pytest.raises(ValueError): await service_action('anything; echo nope')
