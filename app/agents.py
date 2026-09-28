"""Agent system prompts and the supervisor's DELEGATE/TASK/FINAL protocol."""
import re
from dataclasses import dataclass

SUPERVISOR_PROMPT = """You are the Supervisor of a small research team. You never do the research or writing yourself; you coordinate workers and synthesize their results.

Workers:
- researcher: finds and summarizes facts, data, definitions and key perspectives on a topic.
- writer: turns research notes into polished, well-structured Markdown prose.
- reviewer: critiques a draft for accuracy, clarity and gaps, and lists concrete fixes.

Each turn, reply in EXACTLY one of these two formats and nothing else.

To delegate to one worker:
DELEGATE: <researcher|writer|reviewer>
TASK: <specific, self-contained instructions for that worker>

To finish:
FINAL:
<the complete final answer for the user, in Markdown>

Typical flow: researcher, then writer, optionally reviewer (then writer again if the reviewer asked for changes), then FINAL. Your FINAL answer must be the full polished result that synthesizes the workers' output, not a pointer to it."""

WORKER_PROMPTS = {
    "researcher": (
        "You are the Researcher on a small research team. Given a task and any context from teammates, "
        "gather the relevant facts, figures, definitions, key arguments and notable perspectives. "
        "Return concise research notes as Markdown bullet points grouped under short headings. "
        "Flag anything uncertain or likely out of date. Do not write polished prose; that is the Writer's job."
    ),
    "writer": (
        "You are the Writer on a small research team. Turn the research notes in the context into polished, "
        "well-structured Markdown: a clear title, a short introduction, logically ordered sections and a brief "
        "conclusion. Stay faithful to the research and do not invent facts. Apply any reviewer feedback present "
        "in the context. Follow any audience, length or style instructions in the task."
    ),
    "reviewer": (
        "You are the Reviewer on a small research team. Critically review the latest draft in the context for "
        "factual accuracy, clarity, structure and gaps relative to the task. Start with a one-line verdict, "
        "APPROVED or NEEDS CHANGES, then give a numbered list of specific, actionable fixes."
    ),
}


@dataclass
class Decision:
    action: str  # "delegate" | "final"
    worker: str = ""
    task: str = ""
    output: str = ""


# Models sometimes bold the keywords: **DELEGATE:** / **DELEGATE**: -> DELEGATE:
_BOLD = re.compile(r"\*\*\s*(DELEGATE|TASK|FINAL)\s*(?:\*\*)?\s*:\s*(?:\*\*)?", re.I)
_DELEGATE = re.compile(r"^[ \t]*DELEGATE:[ \t]*(\w+).*?^[ \t]*TASK:[ \t]*(.+)", re.I | re.M | re.S)
_FINAL = re.compile(r"^[ \t]*FINAL:[ \t]*(.*)", re.I | re.M | re.S)


def parse_decision(text: str) -> Decision:
    text = _BOLD.sub(r"\1: ", text)
    delegate, final = _DELEGATE.search(text), _FINAL.search(text)
    if delegate and (not final or delegate.start() < final.start()):
        return Decision("delegate", worker=delegate.group(1).lower(), task=delegate.group(2).strip())
    if final and final.group(1).strip():
        return Decision("final", output=final.group(1).strip())
    # No protocol keyword: treat the whole reply as the answer rather than failing the run.
    return Decision("final", output=text.strip())


def build_supervisor_prompt(task: str, history: list[str], iteration: int, max_iterations: int, force: bool = False) -> str:
    work = "\n\n".join(history) or "(nothing yet)"
    prompt = f"User task:\n{task}\n\nIteration {iteration} of {max_iterations}.\n\nWork so far:\n{work}\n\n"
    if force:
        return prompt + (
            "You have reached the iteration limit. You MUST reply now with FINAL: followed by the best complete "
            "answer you can synthesize from the work so far. Do not delegate."
        )
    return prompt + "Reply with DELEGATE/TASK or FINAL."


def build_worker_prompt(task: str, results: list[str]) -> str:
    context = "\n\n".join(results) or "(none yet)"
    return f"## Task\n{task}\n\n## Context from teammates\n{context}"
