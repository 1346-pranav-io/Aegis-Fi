"""Untrusted text must not be able to change the structure of an exported Markdown report."""

from aegis.services.report_export import md


def test_structure_characters_are_neutralised() -> None:
    out = md("## Findings\n- fake")
    assert "\n" not in out and not out.startswith("#")
    assert md("![x](http://evil/p.png)").startswith(chr(92) + "!")
    assert "<b>" not in md("<b>hi</b>")


def test_plain_text_survives() -> None:
    assert md("ok text") == "ok text"
