from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import load_dotenv
from notion_client import Client
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help="Clone and inspect a Notion database tree.")
console = Console()

WINDOWS_FORBIDDEN = r'<>:"/\\|?*'
ENV_FILE: Optional[Path] = None


@dataclass(frozen=True)
class Settings:
    token: str
    database_id: str
    name_property: str = "name"
    dir_property: str = "dir"
    parent_property: str = "parent"


@dataclass(frozen=True)
class Node:
    page_id: str
    name: str
    is_dir: bool
    parent_id: str | None


@dataclass
class CloneStats:
    dirs_created: int = 0
    files_written: int = 0
    files_skipped: int = 0
    files_failed: int = 0


@app.callback()
def main(
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Path to a .env file. If omitted, ngit reads .env from the current directory.",
    )
) -> None:
    """ngit CLI."""
    global ENV_FILE
    ENV_FILE = env_file


def load_settings() -> Settings:
    if ENV_FILE:
        load_dotenv(ENV_FILE)
    else:
        load_dotenv()

    token = os.environ.get("NOTION_API_TOKEN", "").strip()
    database_id = os.environ.get("NOTION_PROJECT_DATABASE_ID", "").strip()

    if not token:
        raise RuntimeError("NOTION_API_TOKEN is not set.")
    if not database_id:
        raise RuntimeError("NOTION_PROJECT_DATABASE_ID is not set.")

    return Settings(
        token=token,
        database_id=database_id,
        name_property=os.getenv("NGIT_NAME_PROPERTY", "name"),
        dir_property=os.getenv("NGIT_DIR_PROPERTY", "dir"),
        parent_property=os.getenv("NGIT_PARENT_PROPERTY", "parent"),
    )


@app.command("clone")
def clone_command(
    root_name: str = typer.Argument(..., help="Root folder name in the Notion database."),
    to: Path = typer.Option(Path("."), "--to", help="Output directory."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
) -> None:
    """Clone one Notion database folder tree to local files."""
    settings = load_settings()
    notion = Client(auth=settings.token)

    console.print("[cyan]Fetching database items...[/cyan]")
    pages = fetch_all_database_pages(notion, settings.database_id)
    nodes = [page_to_node(page, settings) for page in pages]
    children_map = build_children_map(nodes)
    root = find_root(nodes, root_name)

    base_dir = to.resolve()
    stats = CloneStats()

    console.print(f"[cyan]Clone root:[/cyan] {root.name}")
    console.print(f"[cyan]Output to :[/cyan] {base_dir}")
    console.print("")

    clone_node(
        notion=notion,
        node=root,
        children_map=children_map,
        base_dir=base_dir,
        relative_path=Path(root.name),
        dry_run=dry_run,
        force=force,
        stats=stats,
    )

    console.print("")
    console.print("[bold]Result[/bold]")
    console.print(f"dirs created : {stats.dirs_created}")
    console.print(f"files written: {stats.files_written}")
    console.print(f"files skipped: {stats.files_skipped}")
    console.print(f"files failed : {stats.files_failed}")

    if stats.files_failed:
        raise typer.Exit(code=1)


@app.command("list")
def list_command() -> None:
    """List root folders in the Notion repository database."""
    settings = load_settings()
    notion = Client(auth=settings.token)

    console.print("[cyan]Fetching database items...[/cyan]")
    pages = fetch_all_database_pages(notion, settings.database_id)
    nodes = [page_to_node(page, settings) for page in pages]
    roots = list_root_folders(nodes)

    if not roots:
        console.print("[yellow]No root folders found.[/yellow]")
        return

    table = Table(title="Root folders")
    table.add_column("Name", style="bold")
    table.add_column("Page ID", overflow="fold")

    for root in roots:
        table.add_row(root.name, root.page_id)

    console.print(table)


def fetch_all_database_pages(notion: Client, database_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    query_method, id_key, target_id = get_query_method(notion, database_id)

    while True:
        kwargs: dict[str, Any] = {id_key: target_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = query_method(**kwargs)
        results.extend(response["results"])

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def get_query_method(notion: Client, database_id: str):
    if hasattr(notion.databases, "query"):
        return notion.databases.query, "database_id", database_id

    if hasattr(notion, "data_sources") and hasattr(notion.data_sources, "query"):
        data_source_id = resolve_data_source_id(notion, database_id)
        return notion.data_sources.query, "data_source_id", data_source_id

    raise RuntimeError("This notion-client version cannot query databases or data sources.")


def resolve_data_source_id(notion: Client, database_id: str) -> str:
    try:
        database = notion.databases.retrieve(database_id=database_id)
        data_sources = database.get("data_sources") or []
        if data_sources:
            return data_sources[0]["id"]
    except Exception:
        pass
    return database_id


def page_to_node(page: dict[str, Any], settings: Settings) -> Node:
    props = page["properties"]

    if settings.name_property not in props:
        raise KeyError(f"name property not found: {settings.name_property}")

    name = sanitize_filename(extract_title(props[settings.name_property]))
    is_dir = bool(props.get(settings.dir_property, {}).get("checkbox", False))
    parent_id = extract_parent_id(props.get(settings.parent_property))

    return Node(page_id=page["id"], name=name, is_dir=is_dir, parent_id=parent_id)


def extract_title(prop: dict[str, Any]) -> str:
    title_items = prop.get("title", [])
    title = "".join(item.get("plain_text", "") for item in title_items)
    return normalize_notion_title(title)


def normalize_notion_title(title: str) -> str:
    title = title.strip()
    match = re.fullmatch(r"\[(.+?)\]\(https?://.+?\)", title)
    if match:
        return match.group(1).strip()
    return title


def extract_parent_id(prop: dict[str, Any] | None) -> str | None:
    if not prop:
        return None
    relation = prop.get("relation") or []
    if not relation:
        return None
    return relation[0].get("id")


def build_children_map(nodes: list[Node]) -> dict[str, list[Node]]:
    children: dict[str, list[Node]] = {}
    for node in nodes:
        if node.parent_id:
            children.setdefault(node.parent_id, []).append(node)

    for child_nodes in children.values():
        child_nodes.sort(key=lambda n: (not n.is_dir, n.name.lower()))

    return children


def find_root(nodes: list[Node], root_name: str) -> Node:
    normalized_root_name = sanitize_filename(root_name)
    matches = [node for node in nodes if node.name == normalized_root_name and node.is_dir]

    if not matches:
        raise typer.BadParameter(f"root folder not found: {root_name}")
    if len(matches) > 1:
        raise typer.BadParameter(f"root folder is ambiguous: {root_name}")

    return matches[0]


def list_root_folders(nodes: list[Node]) -> list[Node]:
    roots = [node for node in nodes if node.parent_id is None and node.is_dir]
    roots.sort(key=lambda n: n.name.lower())
    return roots


def clone_node(
    notion: Client,
    node: Node,
    children_map: dict[str, list[Node]],
    base_dir: Path,
    relative_path: Path,
    dry_run: bool,
    force: bool,
    stats: CloneStats,
) -> None:
    target_path = safe_join(base_dir, relative_path)

    if node.is_dir:
        if dry_run:
            console.print(f"CREATE DIR  {relative_path}")
        else:
            target_path.mkdir(parents=True, exist_ok=True)
        stats.dirs_created += 1

        for child in children_map.get(node.page_id, []):
            clone_node(
                notion=notion,
                node=child,
                children_map=children_map,
                base_dir=base_dir,
                relative_path=relative_path / child.name,
                dry_run=dry_run,
                force=force,
                stats=stats,
            )
        return

    if target_path.exists() and not force:
        console.print(f"SKIP FILE   {relative_path}")
        stats.files_skipped += 1
        return

    try:
        content = fetch_file_content(notion, node.page_id)
        if dry_run:
            console.print(f"WRITE FILE  {relative_path}")
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8", newline="\n")
        stats.files_written += 1
    except Exception as exc:
        console.print(f"[red]FAILED[/red] {relative_path}: {exc}")
        stats.files_failed += 1


def fetch_file_content(notion: Client, page_id: str) -> str:
    blocks = fetch_all_blocks(notion, page_id)
    code_blocks = [block for block in blocks if block.get("type") == "code"]
    non_code_blocks = [
        block
        for block in blocks
        if block.get("type") != "code" and not is_empty_paragraph_block(block)
    ]

    if non_code_blocks:
        raise RuntimeError("file page must contain only one code block plus empty paragraphs")

    if len(code_blocks) != 1:
        raise RuntimeError("file page must contain exactly one code block")

    code = code_blocks[0]["code"]
    text = rich_text_to_plain(code.get("rich_text", []))
    return ensure_trailing_newline(text)


def is_empty_paragraph_block(block: dict[str, Any]) -> bool:
    if block.get("type") != "paragraph":
        return False

    paragraph = block.get("paragraph", {})
    text = rich_text_to_plain(paragraph.get("rich_text", []))
    return text.strip() == ""


def fetch_all_blocks(notion: Client, page_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = notion.blocks.children.list(**kwargs)
        results.extend(response["results"])

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def rich_text_to_plain(items: list[dict[str, Any]]) -> str:
    return "".join(item.get("plain_text", "") for item in items)


def ensure_trailing_newline(text: str) -> str:
    if text and not text.endswith("\n"):
        return text + "\n"
    return text


def sanitize_filename(name: str) -> str:
    name = normalize_notion_title(name).strip()
    for ch in WINDOWS_FORBIDDEN:
        name = name.replace(ch, "_")
    if name in {"", ".", ".."}:
        raise ValueError("invalid file name")
    return name


def safe_join(base_dir: Path, relative_path: Path) -> Path:
    base_dir = base_dir.resolve()
    path = (base_dir / relative_path).resolve()
    if not path.is_relative_to(base_dir):
        raise ValueError(f"unsafe path: {relative_path}")
    return path


if __name__ == "__main__":
    app()
