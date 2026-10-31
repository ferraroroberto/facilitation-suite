from src.text import fold


def test_fold_drops_case_and_accents_and_groups_sharp_s() -> None:
    assert fold("Álvaro") == fold("ALVARO") == "alvaro"
    assert fold("Straße") == fold("STRASSE") == "strasse"
