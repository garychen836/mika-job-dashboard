"""Problems are printed to the server log (Mika keeps a per-user ops log; this repo has no users)."""


def note(kind: str, text: str, level: str = "info") -> None:
    print(f"[{level}] {kind}: {text}"[:500], flush=True)
