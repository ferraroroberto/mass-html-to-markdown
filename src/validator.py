"""Structure validator (issue #20).

Confirms the short Markdown variant kept the exact skeleton of the full one:
same number of lines, identical frontmatter/headers, identical table attribute
column and bullet labels. Only the prose inside table value cells and feature
bullets may differ. Since both variants are produced by the same template this
holds by construction — the validator is the cheap regression guard that proves
an abbreviated value never reshaped the document (e.g. a stray newline or pipe
splitting a row).
"""

from __future__ import annotations


def _is_table_data_row(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("|") and set(s.replace("|", "").replace("-", "").strip()) != set()


def _is_separator_row(line: str) -> bool:
    return set(line.strip()) <= {"|", "-", " "} and "|" in line


def _first_column(line: str) -> str:
    # "| name | a | b |" -> "name"
    parts = line.split("|")
    return parts[1].strip() if len(parts) > 1 else ""


def _is_bullet(line: str) -> bool:
    return line.lstrip().startswith("- **")


def _bullet_label(line: str) -> str:
    # "- **Product**: value" -> "- **Product**"
    return line.split(":", 1)[0]


def skeleton_problems(full_md: str, short_md: str) -> list[str]:
    """Return a list of structural differences; empty means the skeletons match."""
    full = full_md.splitlines()
    short = short_md.splitlines()
    problems: list[str] = []

    if len(full) != len(short):
        problems.append(
            f"line count differs: full={len(full)} short={len(short)}"
        )
        return problems  # line-by-line comparison is meaningless once lengths drift

    for i, (lf, ls) in enumerate(zip(full, short), start=1):
        if lf == ls:
            continue
        if _is_separator_row(lf) and _is_separator_row(ls):
            continue
        if _is_table_data_row(lf) and _is_table_data_row(ls):
            if _first_column(lf) != _first_column(ls):
                problems.append(
                    f"line {i}: table attribute changed "
                    f"({_first_column(lf)!r} -> {_first_column(ls)!r})"
                )
            continue
        if _is_bullet(lf) and _is_bullet(ls):
            if _bullet_label(lf) != _bullet_label(ls):
                problems.append(
                    f"line {i}: bullet label changed "
                    f"({_bullet_label(lf)!r} -> {_bullet_label(ls)!r})"
                )
            continue
        # A non-value line changed — that is structural drift.
        problems.append(f"line {i}: structural line changed: {lf!r} -> {ls!r}")

    return problems


def assert_same_skeleton(full_md: str, short_md: str) -> None:
    """Raise ValueError if the short variant altered the structure."""
    problems = skeleton_problems(full_md, short_md)
    if problems:
        raise ValueError(
            "Short Markdown skeleton diverged from full:\n  - "
            + "\n  - ".join(problems)
        )
