import json

import pytest
from typer.testing import CliRunner

from radmodeling import settings
from radmodeling.cli import app
from radmodeling.registry import Key, select_keys


def test_settings_default_set_and_unset(user_settings):
    assert settings.path() == user_settings
    assert settings.load() == settings.Settings(1.0, 1.0)
    assert settings.set_value("volume-tolerance", "2.5") == 2.5
    assert settings.load().volume_tolerance == 2.5
    assert json.loads(user_settings.read_text()) == {"volume-tolerance": 2.5}
    settings.unset("volume-tolerance")
    assert settings.load() == settings.Settings()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [("tolerance", "1", "unknown setting"), ("max-discrepancy", "x", "not a number"),
     ("max-discrepancy", "0", "more than 0")],
)  # fmt: skip
def test_settings_reject_bad_values(name, value, message):
    with pytest.raises(ValueError, match=message):
        settings.set_value(name, value)


def test_config_command():
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, ["config", *args])
        assert result.exit_code == 0, result.output
        return result.output

    assert "1 (default)" in run()
    run("max-discrepancy", "3")
    assert run("max-discrepancy").strip() == "3"
    assert "volume-tolerance" in run()
    run("max-discrepancy", "--unset")
    assert run("max-discrepancy").strip() == "1"
    result = runner.invoke(app, ["config", "nope", "1"])
    assert result.exit_code == 1
    assert "unknown setting" in result.output


def test_select_keys():
    keys = [Key(3, 1), Key(3, 2), Key(7, 1), Key(7, 2), Key(8, 1)]
    assert select_keys(keys, "3, 7.2") == [Key(3, 1), Key(3, 2), Key(7, 2)]
    with pytest.raises(ValueError, match="invalid selection"):
        select_keys(keys, "3,a")
