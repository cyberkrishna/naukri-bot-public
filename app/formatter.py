import html

from app.scraper import Job


def format_job(j: Job) -> str:
    title = html.escape(j.title or "Untitled")
    company = html.escape(j.company or "—")
    loc = html.escape(j.location or "—")
    exp = html.escape(j.experience or "—")
    posted = html.escape(j.posted or "")
    return (
        f"<b>{title}</b>\n"
        f"🏢 {company}  ·  📍 {loc}\n"
        f"💼 {exp}  ·  🕒 {posted}\n"
        f"🔎 query: <i>{html.escape(j.query)}</i>\n"
        f'<a href="{j.url}">Open on Naukri</a>'
    )


def chunked(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]
