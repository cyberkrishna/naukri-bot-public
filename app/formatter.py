"""Render a Job as a Telegram HTML message."""
import html
import re

from app.scraper import Job

# Limit how much raw description we show below the structured block.
DESC_SNIPPET_CHARS = 240


def _parse_meta(description: str) -> tuple[dict[str, str], str]:
    """Split description into (meta_dict, body_text).

    New rows are stored as:
        KEY: value\\n
        KEY: value\\n
        ---\\n
        <raw description>

    Old rows (pre this change) don't have the separator — meta_dict is
    empty, body_text is the whole description. Old rows from the JobSpy
    commit may still have a leading "💰 ... / yearly\\n" or "[indeed] ..."
    line baked in; we leave those visible in the snippet rather than
    re-parsing them.
    """
    if not description:
        return {}, ""
    if "\n---\n" not in description:
        return {}, description
    head, _, body = description.partition("\n---\n")
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().upper()
        v = v.strip()
        if k and v:
            meta[k] = v
    return meta, body


_JOB_TYPE_LABELS = {
    "fulltime": "Full-time",
    "full-time": "Full-time",
    "parttime": "Part-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "internship": "Internship",
    "intern": "Internship",
    "temporary": "Temporary",
    "permanent": "Permanent",
}


def _pretty_job_type(s: str) -> str:
    return _JOB_TYPE_LABELS.get(s.lower().strip(), s)


def _snippet(text: str, max_chars: int = DESC_SNIPPET_CHARS) -> str:
    """Trim text to a clean preview: strip markdown noise, collapse whitespace,
    cut at word boundary."""
    if not text:
        return ""
    # Indeed's descriptions are dumped as markdown with aggressive escaping:
    # `\*`, `\-`, `\&amp;`, `\=`, runs of equals signs, ** for bold.
    t = text
    t = re.sub(r"\\([\\\-_*&=+.,()\[\]{}!?#])", r"\1", t)  # un-backslash-escape
    t = re.sub(r"#{2,}\s*", "", t)                         # ## headings
    t = re.sub(r"\*\*+", "", t)                            # ** bold markers
    t = re.sub(r"={3,}", "", t)                            # ====== separators
    t = re.sub(r"-{3,}", "", t)                            # ------ separators
    t = re.sub(r"&amp;", "&", t)                           # double-escaped &
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def format_job(j: Job) -> str:
    title = html.escape(j.title or "Untitled")
    company = html.escape(j.company or "—")
    loc = html.escape(j.location or "—")
    posted = html.escape(j.posted or "")

    meta, body = _parse_meta(j.description or "")

    lines = [f"<b>{title}</b>"]
    lines.append(f"🏢 {company}  ·  📍 {loc}")

    salary = meta.get("SALARY")
    if salary:
        lines.append(f"💰 {html.escape(salary)}")

    if meta.get("REMOTE", "").lower() == "yes":
        lines.append("🏡 Remote OK")

    jtype = meta.get("TYPE")
    if jtype:
        lines.append(f"💼 {html.escape(_pretty_job_type(jtype))}")

    skills = meta.get("SKILLS")
    if skills:
        # Skills can be long — trim to a reasonable line.
        skills_short = skills if len(skills) <= 120 else skills[:117] + "…"
        lines.append(f"✨ {html.escape(skills_short)}")

    if posted:
        src = meta.get("SRC")
        posted_line = f"🕒 {posted}"
        if src:
            posted_line += f"  ·  via {html.escape(src)}"
        lines.append(posted_line)
    elif meta.get("SRC"):
        lines.append(f"via {html.escape(meta['SRC'])}")

    snippet = _snippet(body or j.description or "")
    if snippet:
        lines.append("")  # blank line
        lines.append(html.escape(snippet))

    lines.append("")
    lines.append(f'<a href="{j.url}">🔗 See more details</a>')

    return "\n".join(lines)


def chunked(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]
