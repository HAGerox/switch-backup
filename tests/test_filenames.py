from switchbackup.filenames import backup_filename, safe_name


def test_requested_filename_format():
    assert backup_filename("10.0.0.201", "Core Switch") == "201 - Core Switch.txt"


def test_filename_sanitization():
    assert safe_name("Stage/Rack:1") == "Stage_Rack_1"
