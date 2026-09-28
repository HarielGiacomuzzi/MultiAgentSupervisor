from app.agents import (
    WORKER_PROMPTS,
    Decision,
    build_supervisor_prompt,
    build_worker_prompt,
    parse_decision,
)


def test_has_three_workers():
    assert list(WORKER_PROMPTS) == ["researcher", "writer", "reviewer"]


def test_parses_delegate_with_multiline_task():
    d = parse_decision("DELEGATE: researcher\nTASK: Find the history of X.\nInclude dates.")
    assert d == Decision("delegate", worker="researcher", task="Find the history of X.\nInclude dates.")


def test_delegate_tolerates_case_bold_and_preamble():
    d = parse_decision("Let me start with research.\n**DELEGATE:** Writer\n**TASK:** Draft it")
    assert d == Decision("delegate", worker="writer", task="Draft it")


def test_parses_final_multiline_markdown():
    d = parse_decision("FINAL:\n# Title\n\nBody with **bold**.")
    assert d == Decision("final", output="# Title\n\nBody with **bold**.")


def test_lowercase_final_is_accepted():
    assert parse_decision("final: done").output == "done"


def test_earliest_keyword_wins():
    d = parse_decision("FINAL: answer\nDELEGATE: writer\nTASK: y")
    assert d.action == "final"
    d = parse_decision("DELEGATE: writer\nTASK: polish it\nFINAL: later")
    assert d.action == "delegate"


def test_keyword_mid_sentence_is_not_a_decision():
    d = parse_decision("DELEGATE: writer\nTASK: write the final: summary section")
    assert d == Decision("delegate", worker="writer", task="write the final: summary section")


def test_unparseable_text_becomes_final_answer():
    assert parse_decision("  Just an answer.  ") == Decision("final", output="Just an answer.")


def test_supervisor_prompt_includes_history_and_force_note():
    prompt = build_supervisor_prompt("Explain X", ["[Iteration 1] DELEGATE researcher: a\nResult:\nfacts"], 2, 5)
    assert "Explain X" in prompt and "facts" in prompt and "Iteration 2 of 5" in prompt
    assert "MUST" not in prompt
    assert "MUST" in build_supervisor_prompt("Explain X", [], 5, 5, force=True)


def test_worker_prompt_includes_task_and_results():
    prompt = build_worker_prompt("Write it", ["### researcher (iteration 1)\nfacts"])
    assert "Write it" in prompt and "facts" in prompt
    assert "(none yet)" in build_worker_prompt("Research it", [])
