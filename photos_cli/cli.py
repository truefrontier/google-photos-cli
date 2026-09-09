"""Local read-only Google Photos CLI. Session stays in the Chrome profile."""

import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

import click
from playwright.async_api import async_playwright
from rich.console import Console
from rich.table import Table

HOME = Path.home() / ".photos-cli"
PROFILE = HOME / "chrome-profile"
STEALTH = ["--disable-blink-features=AutomationControlled"]
console = Console()
ALBUM_RE = re.compile(r"/album/([^/?#]+)")
PHOTO_RE = re.compile(r"/photo/([^/?#]+)")
TITLE_JUNK = re.compile(r"\d+\s*items?\s*More options$", re.I)


def has_session() -> bool:
    return PROFILE.exists() and any(PROFILE.iterdir())


def need_session() -> None:
    if not has_session():
        console.print("[red]No session. Run: photos login[/red]")
        raise SystemExit(1)


def clean_title(text: str) -> str:
    text = (text or "").strip()
    text = TITLE_JUNK.sub("", text).strip()
    return re.sub(r"\s+", " ", text)


def album_id(value: str) -> str:
    m = ALBUM_RE.search(value.strip())
    if m:
        return m.group(1)
    if value.strip().startswith("AF1Qip"):
        return value.strip()
    raise click.BadParameter("expected an album id or album URL")


def photo_id(value: str) -> str:
    m = PHOTO_RE.search(value.strip())
    if m:
        return m.group(1)
    if value.strip().startswith("AF1Qip"):
        return value.strip()
    raise click.BadParameter("expected a photo id or photo URL")


async def open_context(playwright, headless: bool):
    return await playwright.chromium.launch_persistent_context(
        str(PROFILE),
        headless=headless,
        channel="chrome",
        args=STEALTH,
    )


async def ensure_photos(page) -> None:
    if "photos.google.com" not in (page.url or ""):
        await page.goto("https://photos.google.com/", wait_until="domcontentloaded")
    if "accounts.google." in page.url or "ServiceLogin" in page.url or "signin" in page.url.lower():
        raise RuntimeError("Session expired. Run: photos login")


async def login() -> None:
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    console.print("Opening Chrome. Sign in to Google Photos. This window closes once Photos loads.")
    async with async_playwright() as p:
        context = await open_context(p, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://photos.google.com/", wait_until="domcontentloaded")
        for _ in range(180):
            await asyncio.sleep(5)
            urls = []
            for pg in context.pages:
                try:
                    urls.append(pg.url)
                except Exception:
                    continue
            if any(
                "photos.google.com" in u
                and "accounts.google." not in u
                and "signin" not in u.lower()
                for u in urls
            ):
                await context.close()
                console.print("[green]Session saved.[/green]")
                return
        await context.close()
        raise RuntimeError("Login timed out. Run: photos login")


async def scrape_albums() -> list[dict]:
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://photos.google.com/albums", wait_until="domcontentloaded")
        await ensure_photos(page)
        await page.wait_for_timeout(5000)
        raw = await page.evaluate(
            """() => {
              const out = [];
              for (const a of document.querySelectorAll('a[href*="/album/"]')) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/album\\/([^/?#]+)/);
                if (!m) continue;
                const title = (a.getAttribute('aria-label') || a.textContent || '').trim();
                out.push({id: m[1], title, url: 'https://photos.google.com/album/' + m[1]});
              }
              const seen = new Set();
              return out.filter(x => (seen.has(x.id) ? false : seen.add(x.id)));
            }"""
        )
        await context.close()
    for item in raw:
        item["title"] = clean_title(item.get("title") or "")
    return raw


async def scrape_photos(url: str, limit: int | None) -> list[dict]:
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        await ensure_photos(page)
        await page.wait_for_timeout(5000)
        # Scroll a bit to load more tiles
        for _ in range(4):
            await page.mouse.wheel(0, 2400)
            await page.wait_for_timeout(800)
        raw = await page.evaluate(
            """() => {
              const out = [];
              for (const a of document.querySelectorAll('a[href*="/photo/"]')) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/photo\\/([^/?#]+)/);
                if (!m) continue;
                const label = (a.getAttribute('aria-label') || '').trim();
                out.push({id: m[1], label, url: 'https://photos.google.com/photo/' + m[1]});
              }
              const seen = new Set();
              return out.filter(x => (seen.has(x.id) ? false : seen.add(x.id)));
            }"""
        )
        await context.close()
    items = []
    for item in raw:
        label = item.get("label") or ""
        kind = ""
        taken_at = ""
        # Labels look like: "Photo - Portrait - Nov 10, 2016, 11:22:34 AM"
        parts = [p.strip() for p in label.split(" - ")]
        if parts:
            kind = parts[0]
        if len(parts) >= 3:
            taken_at = " - ".join(parts[2:])
        items.append(
            {
                "id": item["id"],
                "kind": kind,
                "taken_at": taken_at,
                "label": label,
                "url": item["url"],
            }
        )
        if limit and len(items) >= limit:
            break
    return items


async def scrape_info(vid: str) -> dict:
    async with async_playwright() as p:
        context = await open_context(p, headless=True)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(f"https://photos.google.com/photo/{vid}", wait_until="domcontentloaded")
        await ensure_photos(page)
        await page.wait_for_timeout(4000)
        data = await page.evaluate(
            """() => {
              const title = document.title || '';
              const img = document.querySelector('img[src*="googleusercontent"], img[src*="ggpht"]');
              const labels = [];
              for (const el of document.querySelectorAll('[aria-label]')) {
                const v = (el.getAttribute('aria-label') || '').trim();
                if (/^(Photo|Video|Raw photo)\b/i.test(v) && v.includes(',')) labels.push(v);
              }
              return {
                title,
                aria: labels[0] || '',
                src: img ? img.getAttribute('src') : null,
                url: location.href,
              };
            }"""
        )
        await context.close()
    label = data.get("aria") or data.get("title") or ""
    kind = ""
    taken_at = ""
    parts = [p.strip() for p in label.split(" - ")]
    if parts:
        kind = parts[0]
    if len(parts) >= 3:
        taken_at = " - ".join(parts[2:])
    return {
        "id": vid,
        "kind": kind,
        "taken_at": taken_at,
        "label": label,
        "url": f"https://photos.google.com/photo/{vid}",
        "page_url": data.get("url"),
    }


@click.group()
def main():
    """Unofficial read-only CLI for your Google Photos library."""


@main.command("login")
def login_cmd():
    """Open Chrome so you can sign in. Session stays on this machine."""
    asyncio.run(login())


@main.command()
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@click.option("--id-only", is_flag=True)
def albums(fmt, id_only):
    """List albums / collections."""
    need_session()
    rows = asyncio.run(scrape_albums())
    if id_only:
        for row in rows:
            click.echo(row["id"])
        return
    if fmt == "json":
        click.echo(json.dumps(rows, indent=2))
        return
    table = Table(title="Albums")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    for row in rows:
        table.add_row(row["id"], row["title"])
    console.print(table)
    console.print(f"{len(rows)} albums")


@main.command("list")
@click.option("--album", "album", default=None, help="Album id or URL")
@click.option("--limit", type=int, default=50)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@click.option("--id-only", is_flag=True)
def list_cmd(album, limit, fmt, id_only):
    """List photos in an album, or recent library tiles if no album is given."""
    need_session()
    if album:
        aid = album_id(album)
        url = f"https://photos.google.com/album/{aid}"
    else:
        url = "https://photos.google.com/"
    rows = asyncio.run(scrape_photos(url, limit))
    if id_only:
        for row in rows:
            click.echo(row["id"])
        return
    if fmt == "json":
        click.echo(json.dumps(rows, indent=2))
        return
    table = Table(title="Photos")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Kind", style="green")
    table.add_column("Taken", style="white")
    for row in rows:
        table.add_row(row["id"], row.get("kind") or "", row.get("taken_at") or "")
    console.print(table)
    console.print(f"{len(rows)} photos")


@main.command()
@click.argument("query")
@click.option("--limit", type=int, default=50)
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
@click.option("--id-only", is_flag=True)
def search(query, limit, fmt, id_only):
    """Search your library."""
    need_session()
    url = f"https://photos.google.com/search/{quote(query)}"
    rows = asyncio.run(scrape_photos(url, limit))
    if id_only:
        for row in rows:
            click.echo(row["id"])
        return
    if fmt == "json":
        click.echo(json.dumps(rows, indent=2))
        return
    table = Table(title=f'Search: {query}')
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Kind", style="green")
    table.add_column("Taken", style="white")
    for row in rows:
        table.add_row(row["id"], row.get("kind") or "", row.get("taken_at") or "")
    console.print(table)
    console.print(f"{len(rows)} photos")


@main.command()
@click.argument("photo")
@click.option("--format", "fmt", type=click.Choice(["table", "json"]), default="table")
def info(photo, fmt):
    """Show one photo or video."""
    need_session()
    vid = photo_id(photo)
    data = asyncio.run(scrape_info(vid))
    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return
    table = Table(title=data.get("label") or vid)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    for key in ("id", "kind", "taken_at", "url"):
        table.add_row(key, "" if data.get(key) is None else str(data.get(key)))
    console.print(table)


if __name__ == "__main__":
    main()
