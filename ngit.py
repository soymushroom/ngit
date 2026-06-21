from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TypedDict, cast

import typer
from dotenv import load_dotenv
from notion_client import Client
from rich.console import Console
from rich.progress import track
from rich.table import Table

app = typer.Typer(no_args_is_help=True, help="Clone, pull, and push a Notion database tree.")
console = Console()

WINDOWS_FORBIDDEN = r'<>:"/\\|?*'
TEXT_CHUNK_SIZE = 1900
ENV_FILE: Optional[Path] = None


class NotionListResponse(TypedDict):
    results: list[dict[str, Any]]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True)
class Settings:
    token: str
    database_id: str
    name_property: str = "name"
    dir_property: str = "dir"
    parent_property: str = "parent"
    dir_page_icon: str = "icons/folder_yellow"
    file_page_icon: str = "icons/document_blue"


@dataclass(frozen=True)
class Node:
    page_id: str
    name: str
    is_dir: bool
    parent_id: str | None


@dataclass
class SyncStats:
    dirs_created: int = 0
    files_written: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    pages_created: int = 0
    pages_updated: int = 0
    pages_skipped: int = 0
    pages_deleted: int = 0
    paths_ignored: int = 0
    files_deleted: int = 0
    dirs_deleted: int = 0


class NgitPermissionError(RuntimeError):
    """Raised when the Notion integration does not have required capabilities."""


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


@app.command("list")
def list_command() -> None:
    """List root folders in the Notion repository database."""
    settings = load_settings()
    notion = Client(auth=settings.token)

    print_step(1, 3, "Fetching database items")
    nodes = load_nodes(notion, settings)
    print_step(2, 3, "Collecting root folders")
    roots = list_root_folders(nodes)

    if not roots:
        console.print("[yellow]No root folders found.[/yellow]")
        print_step(3, 3, "Finished")
        return

    table = Table(title="Root folders")
    table.add_column("Name", style="bold")
    table.add_column("Page ID", overflow="fold")

    for root in roots:
        table.add_row(root.name, root.page_id)

    print_step(3, 3, "Finished")
    console.print(table)


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

    print_step(1, 4, "Fetching database items")
    nodes = load_nodes(notion, settings)
    print_step(2, 4, "Building Notion tree")
    children_map = build_children_map(nodes)
    root = find_root(nodes, root_name)

    base_dir = to.resolve()
    stats = SyncStats()

    console.print(f"[cyan]Clone root:[/cyan] {root.name}")
    console.print(f"[cyan]Output to :[/cyan] {base_dir}")
    console.print("")

    print_step(3, 4, "Syncing Notion to local")
    sync_notion_to_local(
        notion=notion,
        node=root,
        children_map=children_map,
        base_dir=base_dir,
        relative_path=Path(root.name),
        dry_run=dry_run,
        force=force,
        stats=stats,
    )

    print_step(4, 4, "Finished")
    print_local_result(stats)
    exit_if_failed(stats)


@app.command("pull")
def pull_command(
    paths: Optional[list[Path]] = typer.Argument(
        None,
        help="Optional repository-relative folder or file paths to pull. Multiple paths can be specified.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing files."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files and delete extra local files in the target scope."),
) -> None:
    """Pull Notion contents into the current Git repository root."""
    settings = load_settings()
    notion = Client(auth=settings.token)
    repo_root = find_git_root()

    print_step(1, 5, "Fetching database items")
    nodes = load_nodes(notion, settings)
    print_step(2, 5, "Building Notion tree")
    children_map = build_children_map(nodes)
    root = find_root(nodes, repo_root.name)
    path_map = build_notion_path_map(root, children_map)

    target_rels = normalize_repo_paths(paths)

    stats = SyncStats()

    console.print(f"[cyan]Repository root:[/cyan] {repo_root}")
    console.print(f"[cyan]Notion root    :[/cyan] {root.name}")
    console.print(f"[cyan]Pull targets   :[/cyan] {format_targets(target_rels)}")
    console.print("")

    print_step(3, 5, "Resolving pull targets")
    target_nodes = [
        (target_rel, get_notion_node_by_path(path_map, target_rel))
        for target_rel in target_rels
    ]

    print_step(4, 5, "Syncing Notion to local")
    pull_items: list[tuple[Node, Path]] = []
    delete_candidates: list[tuple[Path, bool]] = []

    for target_rel, target_node in target_nodes:
        pull_items.extend(flatten_notion_items(target_node, children_map, target_rel))

        if force and target_node.is_dir:
            expected_paths = build_expected_local_paths(target_node, children_map, target_rel)
            delete_candidates.extend(
                build_extra_local_path_candidates(
                    base_dir=repo_root,
                    target_rel=target_rel,
                    expected_paths=expected_paths,
                )
            )

    for node, rel_path in track(pull_items, description="Pulling items"):
        sync_notion_item_to_local(
            notion=notion,
            node=node,
            base_dir=repo_root,
            relative_path=rel_path,
            dry_run=dry_run,
            force=force,
            stats=stats,
            log_items=False,
        )

    if force and delete_candidates:
        ignored_paths = git_ignored_paths(repo_root, [rel_path for rel_path, _ in delete_candidates])
        ignored_keys = {path_key(path) for path in ignored_paths}
        delete_items = [
            (rel_path, is_dir)
            for rel_path, is_dir in delete_candidates
            if not is_path_covered_by_keys(rel_path, ignored_keys)
        ]

        for rel_path, is_dir in track(delete_items, description="Deleting extra local paths"):
            delete_extra_local_path(
                base_dir=repo_root,
                rel_path=rel_path,
                is_dir=is_dir,
                dry_run=dry_run,
                stats=stats,
                log_items=False,
            )

    print_step(5, 5, "Finished")
    print_local_result(stats)
    exit_if_failed(stats)


@app.command("push")
def push_command(
    paths: Optional[list[Path]] = typer.Argument(
        None,
        help="Optional repository-relative folder or file paths to push. Multiple paths can be specified.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview Notion changes without writing."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete Notion pages in the target scope when the local path no longer exists.",
    ),
) -> None:
    """Push local files from the current Git repository root to Notion."""
    settings = load_settings()
    notion = Client(auth=settings.token)
    repo_root = find_git_root()

    print_step(1, 6, "Fetching database items")
    nodes = load_nodes(notion, settings)
    print_step(2, 6, "Building Notion tree")
    children_map = build_children_map(nodes)
    root = find_root(nodes, repo_root.name)
    notion_path_map = build_notion_path_map(root, children_map)

    target_rels = normalize_repo_paths(paths)

    for target_rel in target_rels:
        target_abs = safe_join(repo_root, target_rel)
        if not target_abs.exists() and not force:
            raise typer.BadParameter(f"local path does not exist: {target_rel.as_posix()}")

    stats = SyncStats()

    console.print(f"[cyan]Repository root:[/cyan] {repo_root}")
    console.print(f"[cyan]Notion root    :[/cyan] {root.name}")
    console.print(f"[cyan]Push targets   :[/cyan] {format_targets(target_rels)}")
    console.print("")

    print_step(3, 6, "Collecting local paths")
    local_paths: dict[Path, bool] = {}
    for target_rel in track(target_rels, description="Collecting targets"):
        local_paths.update(collect_push_paths(repo_root, target_rel, stats, log_items=False))

    try:
        print_step(4, 6, "Writing local changes to Notion")
        push_items = sorted(local_paths.items(), key=lambda item: (len(item[0].parts), item[0].as_posix()))
        push_items = [(rel_path, is_dir) for rel_path, is_dir in push_items if rel_path != Path(".")]
        for rel_path, is_dir in track(push_items, description="Pushing items"):
            push_one_path(
                notion=notion,
                settings=settings,
                repo_root=repo_root,
                rel_path=rel_path,
                is_dir=is_dir,
                notion_path_map=notion_path_map,
                dry_run=dry_run,
                force=force,
                stats=stats,
                log_items=False,
            )

        print_step(5, 6, "Deleting missing Notion pages")
        if force:
            for target_rel in track(target_rels, description="Checking deletes"):
                delete_missing_notion_pages(
                    notion=notion,
                    repo_root=repo_root,
                    root=root,
                    target_rel=target_rel,
                    target_node=notion_path_map.get(path_key(target_rel)),
                    notion_path_map=notion_path_map,
                    local_paths=local_paths,
                    dry_run=dry_run,
                    stats=stats,
                    log_items=False,
                )
        else:
            console.print("SKIP DELETE --force not specified")
    except NgitPermissionError as exc:
        console.print("")
        console.print("[red]Permission error[/red]")
        console.print(str(exc))
        raise typer.Exit(code=1)

    print_step(6, 6, "Finished")
    print_push_result(stats)
    exit_if_failed(stats)


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
        dir_page_icon=os.getenv("NGIT_DIR_PAGE_ICON", "icons/folder_yellow"),
        file_page_icon=os.getenv("NGIT_FILE_PAGE_ICON", "icons/document_blue"),
    )


def load_nodes(notion: Client, settings: Settings) -> list[Node]:
    pages = fetch_all_database_pages(notion, settings.database_id)
    return [page_to_node(page, settings) for page in pages]


def fetch_all_database_pages(notion: Client, database_id: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor: str | None = None
    query_method, id_key, target_id = get_query_method(notion, database_id)

    while True:
        kwargs: dict[str, Any] = {id_key: target_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = cast(NotionListResponse, query_method(**kwargs))
        results.extend(response["results"])

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return results


def get_query_method(notion: Client, database_id: str) -> tuple[Callable[..., Any], str, str]:
    databases = cast(Any, notion.databases)
    if hasattr(databases, "query"):
        return databases.query, "database_id", database_id

    data_sources = cast(Any, getattr(notion, "data_sources", None))
    if data_sources is not None and hasattr(data_sources, "query"):
        data_source_id = resolve_data_source_id(notion, database_id)
        return data_sources.query, "data_source_id", data_source_id

    raise RuntimeError("This notion-client version cannot query databases or data sources.")


def resolve_data_source_id(notion: Client, database_id: str) -> str:
    try:
        database = cast(dict[str, Any], notion.databases.retrieve(database_id=database_id))
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


def build_notion_path_map(root: Node, children_map: dict[str, list[Node]]) -> dict[tuple[str, ...], Node]:
    path_map: dict[tuple[str, ...], Node] = {(): root}

    def visit(node: Node, current: tuple[str, ...]) -> None:
        for child in children_map.get(node.page_id, []):
            child_path = current + (child.name,)
            path_map[child_path] = child
            if child.is_dir:
                visit(child, child_path)

    visit(root, ())
    return path_map


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


def get_notion_node_by_path(path_map: dict[tuple[str, ...], Node], rel_path: Path) -> Node:
    key = path_key(rel_path)
    if key not in path_map:
        raise typer.BadParameter(f"Notion path not found: {rel_path.as_posix()}")
    return path_map[key]


def sync_notion_to_local(
    notion: Client,
    node: Node,
    children_map: dict[str, list[Node]],
    base_dir: Path,
    relative_path: Path,
    dry_run: bool,
    force: bool,
    stats: SyncStats,
    log_items: bool = True,
) -> None:
    target_path = safe_join(base_dir, relative_path)

    if node.is_dir:
        if dry_run:
            if log_items:
                console.print(f"CREATE DIR  {display_path(relative_path)}")
        else:
            target_path.mkdir(parents=True, exist_ok=True)
        stats.dirs_created += 1

        for child in children_map.get(node.page_id, []):
            sync_notion_to_local(
                notion=notion,
                node=child,
                children_map=children_map,
                base_dir=base_dir,
                relative_path=relative_path / child.name,
                dry_run=dry_run,
                force=force,
                stats=stats,
                log_items=log_items,
            )
        return

    if target_path.exists() and not force:
        if log_items:
            console.print(f"SKIP FILE   {display_path(relative_path)}")
        stats.files_skipped += 1
        return

    try:
        content = fetch_file_content(notion, node.page_id)
        if dry_run:
            if log_items:
                console.print(f"WRITE FILE  {display_path(relative_path)}")
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
        stats.files_written += 1
    except Exception as exc:
        console.print(f"[red]FAILED[/red] {display_path(relative_path)}: {exc}")
        stats.files_failed += 1


def flatten_notion_items(
    node: Node,
    children_map: dict[str, list[Node]],
    relative_path: Path,
) -> list[tuple[Node, Path]]:
    items: list[tuple[Node, Path]] = [(node, relative_path)]

    if node.is_dir:
        for child in children_map.get(node.page_id, []):
            items.extend(flatten_notion_items(child, children_map, relative_path / child.name))

    return items


def sync_notion_item_to_local(
    notion: Client,
    node: Node,
    base_dir: Path,
    relative_path: Path,
    dry_run: bool,
    force: bool,
    stats: SyncStats,
    log_items: bool = True,
) -> None:
    target_path = safe_join(base_dir, relative_path)

    if node.is_dir:
        if dry_run:
            if log_items:
                console.print(f"CREATE DIR  {display_path(relative_path)}")
        else:
            target_path.mkdir(parents=True, exist_ok=True)
        stats.dirs_created += 1
        return

    if target_path.exists() and not force:
        if log_items:
            console.print(f"SKIP FILE   {display_path(relative_path)}")
        stats.files_skipped += 1
        return

    try:
        content = fetch_file_content(notion, node.page_id)
        if dry_run:
            if log_items:
                console.print(f"WRITE FILE  {display_path(relative_path)}")
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
        stats.files_written += 1
    except Exception as exc:
        console.print(f"[red]FAILED[/red] {display_path(relative_path)}: {exc}")
        stats.files_failed += 1


def build_expected_local_paths(
    node: Node,
    children_map: dict[str, list[Node]],
    relative_path: Path,
) -> dict[Path, bool]:
    expected: dict[Path, bool] = {normalize_repo_path(relative_path): node.is_dir}

    if not node.is_dir:
        return expected

    for child in children_map.get(node.page_id, []):
        expected.update(
            build_expected_local_paths(
                node=child,
                children_map=children_map,
                relative_path=relative_path / child.name,
            )
        )

    return expected


def build_extra_local_path_candidates(
    base_dir: Path,
    target_rel: Path,
    expected_paths: dict[Path, bool],
) -> list[tuple[Path, bool]]:
    target_path = safe_join(base_dir, target_rel)
    candidates: list[tuple[Path, bool]] = []

    if not target_path.exists() or not target_path.is_dir():
        return candidates

    for current_dir, dir_names, file_names in os.walk(target_path, topdown=False):
        current_abs = Path(current_dir)

        for filename in file_names:
            rel_path = relative_to_repo(base_dir, current_abs / filename)
            if should_preserve_local_path(rel_path):
                continue
            if rel_path in expected_paths:
                continue

            candidates.append((rel_path, False))

        for dirname in dir_names:
            rel_path = relative_to_repo(base_dir, current_abs / dirname)
            if should_preserve_local_path(rel_path):
                continue
            if rel_path in expected_paths:
                continue

            candidates.append((rel_path, True))

    return candidates


def delete_extra_local_path(
    base_dir: Path,
    rel_path: Path,
    is_dir: bool,
    dry_run: bool,
    stats: SyncStats,
    log_items: bool = True,
) -> None:
    if dry_run:
        if log_items:
            console.print(f"DELETE {'DIR ' if is_dir else 'FILE'} {display_path(rel_path)}")
    else:
        target_path = safe_join(base_dir, rel_path)
        if is_dir:
            shutil.rmtree(target_path)
        else:
            target_path.unlink()

    if is_dir:
        stats.dirs_deleted += 1
    else:
        stats.files_deleted += 1


def should_preserve_local_path(rel_path: Path) -> bool:
    return ".git" in rel_path.parts


def collect_push_paths(repo_root: Path, target_rel: Path, stats: SyncStats, log_items: bool = True) -> dict[Path, bool]:
    target_abs = safe_join(repo_root, target_rel)
    candidates: dict[Path, bool] = {}

    if not target_abs.exists():
        return candidates

    if target_abs.is_file():
        candidates[target_rel] = False
    else:
        candidates[target_rel] = True

        for current_dir, dir_names, file_names in os.walk(target_abs):
            current_abs = Path(current_dir)
            current_rel = relative_to_repo(repo_root, current_abs)

            dir_names[:] = sorted(dirname for dirname in dir_names if dirname != ".git")

            for dirname in dir_names:
                rel = normalize_repo_path(current_rel / dirname)
                candidates[rel] = True

            for filename in sorted(file_names):
                rel = normalize_repo_path(current_rel / filename)
                candidates[rel] = False

    ignored_paths = git_ignored_paths(repo_root, list(candidates.keys()))
    ignored_keys = {path_key(path) for path in ignored_paths}
    paths: dict[Path, bool] = {}

    for rel_path, is_dir in sorted(candidates.items(), key=lambda item: (len(item[0].parts), item[0].as_posix())):
        key = path_key(rel_path)
        has_ignored_parent = any(
            ignored_key != key and key[: len(ignored_key)] == ignored_key
            for ignored_key in ignored_keys
        )

        if rel_path in ignored_paths:
            if not has_ignored_parent:
                if log_items:
                    console.print(f"IGNORE      {display_path(rel_path)}")
                stats.paths_ignored += 1
            continue

        if has_ignored_parent:
            continue

        paths[rel_path] = is_dir

    return paths


def push_one_path(
    notion: Client,
    settings: Settings,
    repo_root: Path,
    rel_path: Path,
    is_dir: bool,
    notion_path_map: dict[tuple[str, ...], Node],
    dry_run: bool,
    force: bool,
    stats: SyncStats,
    log_items: bool = True,
) -> None:
    key = path_key(rel_path)
    existing = notion_path_map.get(key)

    if existing:
        if existing.is_dir != is_dir:
            console.print(f"[red]FAILED[/red] {display_path(rel_path)}: local/Notion type mismatch")
            stats.files_failed += 1
            return

        if is_dir:
            return

        if not force:
            if log_items:
                console.print(f"SKIP PAGE   {display_path(rel_path)}")
            stats.pages_skipped += 1
            return

        try:
            content = safe_join(repo_root, rel_path).read_text(encoding="utf-8")
            if dry_run:
                if log_items:
                    console.print(f"UPDATE PAGE {display_path(rel_path)}")
            else:
                replace_file_page_content(notion, existing.page_id, content, rel_path)
            stats.pages_updated += 1
        except Exception as exc:
            console.print(f"[red]FAILED[/red] {display_path(rel_path)}: {exc}")
            stats.files_failed += 1
        return

    parent_key = path_key(rel_path.parent)
    parent = notion_path_map.get(parent_key)

    if not parent:
        console.print(f"[red]FAILED[/red] {display_path(rel_path)}: parent page not found")
        stats.files_failed += 1
        return

    if not parent.is_dir:
        console.print(f"[red]FAILED[/red] {display_path(rel_path)}: parent is not a directory page")
        stats.files_failed += 1
        return

    try:
        if dry_run:
            if log_items:
                console.print(f"CREATE PAGE {'DIR ' if is_dir else 'FILE'} {display_path(rel_path)}")
            created = Node(
                page_id=f"dry-run:{rel_path.as_posix()}",
                name=rel_path.name,
                is_dir=is_dir,
                parent_id=parent.page_id,
            )
        else:
            created = create_notion_page(
                notion=notion,
                settings=settings,
                name=rel_path.name,
                is_dir=is_dir,
                parent_node=parent,
                content=safe_join(repo_root, rel_path).read_text(encoding="utf-8") if not is_dir else None,
                rel_path=rel_path,
            )
        notion_path_map[key] = created
        stats.pages_created += 1
    except Exception as exc:
        if is_restricted_resource_error(exc):
            raise NgitPermissionError(build_push_permission_help()) from exc
        console.print(f"[red]FAILED[/red] {display_path(rel_path)}: {exc}")
        stats.files_failed += 1


def delete_missing_notion_pages(
    notion: Client,
    repo_root: Path,
    root: Node,
    target_rel: Path,
    target_node: Node | None,
    notion_path_map: dict[tuple[str, ...], Node],
    local_paths: dict[Path, bool],
    dry_run: bool,
    stats: SyncStats,
    log_items: bool = True,
) -> None:
    local_keys = {path_key(path) for path in local_paths.keys()}

    if target_node is None and target_rel != Path("."):
        target_key = path_key(target_rel)
        candidates = [(key, node) for key, node in notion_path_map.items() if key == target_key or key[: len(target_key)] == target_key]
    else:
        target_key = path_key(target_rel)
        candidates = [(key, node) for key, node in notion_path_map.items() if key and (target_rel == Path(".") or key == target_key or key[: len(target_key)] == target_key)]

    for key, node in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if node.page_id == root.page_id:
            continue

        rel = Path(*key) if key else Path(".")
        if key in local_keys:
            continue

        if is_git_ignored(repo_root, rel):
            continue

        try:
            if dry_run:
                if log_items:
                    console.print(f"DELETE PAGE {display_path(rel)}")
            else:
                notion.pages.update(page_id=node.page_id, archived=True)
            stats.pages_deleted += 1
        except Exception as exc:
            if is_restricted_resource_error(exc):
                raise NgitPermissionError(build_push_permission_help()) from exc
            console.print(f"[red]FAILED[/red] {display_path(rel)}: {exc}")
            stats.files_failed += 1


def create_notion_page(
    notion: Client,
    settings: Settings,
    name: str,
    is_dir: bool,
    parent_node: Node,
    content: str | None,
    rel_path: Path,
) -> Node:
    kwargs: dict[str, Any] = {
        "parent": {"database_id": settings.database_id},
        "properties": build_page_properties(settings, name, is_dir, parent_node.page_id),
        "icon": page_icon_payload(settings, is_dir),
    }

    if not is_dir:
        kwargs["children"] = [code_block(content or "", rel_path)]

    try:
        page = cast(dict[str, Any], notion.pages.create(**kwargs))
    except Exception as exc:
        if not is_icon_error(exc):
            raise

        kwargs["icon"] = fallback_icon_payload(is_dir)
        page = cast(dict[str, Any], notion.pages.create(**kwargs))
    return Node(
        page_id=page["id"],
        name=sanitize_filename(name),
        is_dir=is_dir,
        parent_id=parent_node.page_id,
    )


def build_page_properties(settings: Settings, name: str, is_dir: bool, parent_page_id: str) -> dict[str, Any]:
    return {
        settings.name_property: {
            "title": [{"type": "text", "text": {"content": name}}],
        },
        settings.dir_property: {"checkbox": is_dir},
        settings.parent_property: {"relation": [{"id": parent_page_id}]},
    }


def page_icon_payload(settings: Settings, is_dir: bool) -> dict[str, Any]:
    icon = settings.dir_page_icon if is_dir else settings.file_page_icon

    if icon.startswith("emoji:"):
        return {"type": "emoji", "emoji": icon.removeprefix("emoji:")}

    if icon.startswith("http://") or icon.startswith("https://"):
        return {"type": "external", "external": {"url": icon}}

    if icon.startswith("icons/"):
        native_icon = native_icon_payload(icon)
        if native_icon:
            return native_icon

    return fallback_icon_payload(is_dir)


def native_icon_payload(icon: str) -> dict[str, Any] | None:
    value = icon.removeprefix("icons/")
    colors = {"gray", "lightgray", "brown", "yellow", "orange", "green", "blue", "purple", "pink", "red"}

    if "_" not in value:
        return None

    name, color = value.rsplit("_", 1)
    if not name or color not in colors:
        return None

    return {
        "type": "icon",
        "icon": {
            "name": name,
            "color": color,
        },
    }


def fallback_icon_payload(is_dir: bool) -> dict[str, Any]:
    return {"type": "emoji", "emoji": "📁" if is_dir else "📄"}


def is_icon_error(exc: Exception) -> bool:
    return "icon" in str(exc).lower()


def replace_file_page_content(notion: Client, page_id: str, content: str, rel_path: Path) -> None:
    blocks = fetch_all_blocks(notion, page_id)
    validate_file_page_blocks(blocks)

    for block in blocks:
        notion.blocks.delete(block_id=block["id"])

    notion.blocks.children.append(
        block_id=page_id,
        children=[code_block(content, rel_path)],
    )


def code_block(content: str, rel_path: Path) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "code",
        "code": {
            "rich_text": text_to_rich_text(content),
            "language": guess_code_language(rel_path),
        },
    }


def text_to_rich_text(text: str) -> list[dict[str, Any]]:
    if text == "":
        return []

    return [
        {"type": "text", "text": {"content": text[index : index + TEXT_CHUNK_SIZE]}}
        for index in range(0, len(text), TEXT_CHUNK_SIZE)
    ]


def guess_code_language(path: Path) -> str:
    suffix = path.suffix.lower()
    mapping = {
        ".bat": "batch",
        ".c": "c",
        ".cpp": "c++",
        ".cs": "c#",
        ".css": "css",
        ".csv": "plain text",
        ".env": "plain text",
        ".go": "go",
        ".html": "html",
        ".ini": "plain text",
        ".java": "java",
        ".js": "javascript",
        ".json": "json",
        ".jsx": "javascript",
        ".md": "markdown",
        ".ps1": "powershell",
        ".py": "python",
        ".rb": "ruby",
        ".rs": "rust",
        ".sh": "shell",
        ".toml": "toml",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".txt": "plain text",
        ".xml": "xml",
        ".yaml": "yaml",
        ".yml": "yaml",
    }
    return mapping.get(suffix, "plain text")


def fetch_file_content(notion: Client, page_id: str) -> str:
    blocks = fetch_all_blocks(notion, page_id)
    validate_file_page_blocks(blocks)

    code_blocks = [block for block in blocks if block.get("type") == "code"]
    code = code_blocks[0]["code"]
    text = rich_text_to_plain(code.get("rich_text", []))
    return ensure_trailing_newline(text)


def validate_file_page_blocks(blocks: list[dict[str, Any]]) -> None:
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

        response = cast(NotionListResponse, notion.blocks.children.list(**kwargs))
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


def is_restricted_resource_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "")
    return "RestrictedResource" in str(code) or "Insufficient permissions" in str(exc)


def build_push_permission_help() -> str:
    return (
        "Notion integration permissions are insufficient for push.\n"
        "\n"
        "Check the integration used by NOTION_API_TOKEN:\n"
        "- Content: Read content\n"
        "- Content: Insert content\n"
        "- Content: Update content\n"
        "\n"
        "After changing capabilities, reconnect the integration to the target database/page if needed.\n"
        "Then rerun `ngit push --dry-run` before running `ngit push`."
    )


def find_git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError("not inside a Git repository")

    return Path(result.stdout.strip()).resolve()


def is_git_ignored(repo_root: Path, rel_path: Path) -> bool:
    rel_path = normalize_repo_path(rel_path)
    return rel_path in git_ignored_paths(repo_root, [rel_path])


def git_ignored_paths(repo_root: Path, rel_paths: list[Path]) -> set[Path]:
    ignored: set[Path] = set()
    candidates: list[str] = []
    candidate_to_path: dict[str, Path] = {}

    normalized_paths = [normalize_repo_path(rel_path) for rel_path in rel_paths]

    for rel_path in normalized_paths:
        if rel_path == Path("."):
            continue

        if ".git" in rel_path.parts:
            ignored.add(rel_path)
            continue

        path_candidate = rel_path.as_posix()
        candidates.append(path_candidate)
        candidate_to_path[path_candidate] = rel_path

        if path_looks_like_directory(repo_root, rel_path):
            dir_candidate = path_candidate.rstrip("/") + "/"
            candidates.append(dir_candidate)
            candidate_to_path[dir_candidate] = rel_path

    if candidates:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--no-index", "--stdin"],
            input="\n".join(candidates) + "\n",
            text=True,
            capture_output=True,
            check=False,
        )

        if result.returncode in {0, 1}:
            for line in result.stdout.splitlines():
                rel_path = candidate_to_path.get(line.strip())
                if rel_path:
                    ignored.add(rel_path)

    ignored.update(gitignore_fallback_ignored_paths(repo_root, normalized_paths))
    return ignored


def path_looks_like_directory(repo_root: Path, rel_path: Path) -> bool:
    target_path = safe_join(repo_root, rel_path)
    if target_path.exists():
        return target_path.is_dir()

    return rel_path.suffix == "" and not rel_path.name.startswith(".")


def is_path_covered_by_keys(rel_path: Path, parent_keys: set[tuple[str, ...]]) -> bool:
    key = path_key(rel_path)
    return any(parent_key == key or key[: len(parent_key)] == parent_key for parent_key in parent_keys)


def gitignore_fallback_ignored_paths(repo_root: Path, rel_paths: list[Path]) -> set[Path]:
    ignored: set[Path] = set()
    rules_cache: dict[Path, list[tuple[str, bool, bool]]] = {}

    for rel_path in rel_paths:
        rel_path = normalize_repo_path(rel_path)

        if rel_path == Path(".") or ".git" in rel_path.parts:
            continue

        is_ignored = False
        for ignore_dir in gitignore_rule_dirs(rel_path):
            rules = load_gitignore_rules(repo_root, ignore_dir, rules_cache)
            if not rules:
                continue

            sub_path = rel_path if ignore_dir == Path(".") else rel_path.relative_to(ignore_dir)
            for pattern, negated, dir_only in rules:
                if gitignore_rule_matches(sub_path, pattern, dir_only):
                    is_ignored = not negated

        if is_ignored:
            ignored.add(rel_path)

    return ignored


def gitignore_rule_dirs(rel_path: Path) -> list[Path]:
    if rel_path == Path("."):
        return [Path(".")]

    dirs = [Path(".")]
    current = Path(".")
    parent_parts = rel_path.parent.parts if rel_path.parent != Path(".") else ()

    for part in parent_parts:
        current = current / part
        dirs.append(current)

    return dirs


def load_gitignore_rules(
    repo_root: Path,
    ignore_dir: Path,
    rules_cache: dict[Path, list[tuple[str, bool, bool]]],
) -> list[tuple[str, bool, bool]]:
    if ignore_dir in rules_cache:
        return rules_cache[ignore_dir]

    gitignore_path = safe_join(repo_root, ignore_dir / ".gitignore")
    rules: list[tuple[str, bool, bool]] = []

    if gitignore_path.exists() and gitignore_path.is_file():
        for raw_line in gitignore_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()

            if not line:
                continue

            dir_only = line.endswith("/")
            line = line.rstrip("/")
            if line.startswith("/"):
                line = line[1:]

            if line:
                rules.append((line, negated, dir_only))

    rules_cache[ignore_dir] = rules
    return rules


def gitignore_rule_matches(sub_path: Path, pattern: str, dir_only: bool) -> bool:
    sub_posix = sub_path.as_posix()
    parts = sub_path.parts
    anchored = "/" in pattern

    if dir_only:
        if anchored:
            return sub_posix == pattern or sub_posix.startswith(pattern.rstrip("/") + "/")

        return any(fnmatch.fnmatch(part, pattern) for part in parts)

    if anchored:
        return fnmatch.fnmatch(sub_posix, pattern)

    return any(fnmatch.fnmatch(part, pattern) for part in parts)


def normalize_repo_paths(paths: Optional[list[Path]]) -> list[Path]:
    if not paths:
        return [Path(".")]

    normalized = [normalize_repo_path(path) for path in paths]
    return dedupe_target_paths(normalized)


def dedupe_target_paths(paths: list[Path]) -> list[Path]:
    by_key = {path_key(path): path for path in paths}
    ordered = sorted(by_key.values(), key=lambda path: (len(path_key(path)), path.as_posix()))

    result: list[Path] = []
    result_keys: list[tuple[str, ...]] = []

    for path in ordered:
        key = path_key(path)
        if any(parent_key == () or key[: len(parent_key)] == parent_key for parent_key in result_keys):
            continue

        result.append(path)
        result_keys.append(key)

    return result


def format_targets(paths: list[Path]) -> str:
    return ", ".join(display_path(path) for path in paths)


def normalize_repo_path(path: Path | None) -> Path:
    if path is None:
        return Path(".")

    normalized = Path(path)
    if normalized.is_absolute():
        raise typer.BadParameter("path must be relative to the Git repository root")

    parts = [part for part in normalized.parts if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise typer.BadParameter("path must not contain '..'")

    if not parts:
        return Path(".")

    return Path(*parts)


def relative_to_repo(repo_root: Path, path: Path) -> Path:
    rel = path.resolve().relative_to(repo_root.resolve())
    return normalize_repo_path(rel)


def path_key(path: Path) -> tuple[str, ...]:
    path = normalize_repo_path(path)
    if path == Path("."):
        return ()
    return tuple(path.parts)


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


def display_path(path: Path) -> str:
    if path == Path("."):
        return "."
    return path.as_posix()


def print_step(current: int, total: int, message: str) -> None:
    console.print(f"[cyan][{current}/{total}] {message}...[/cyan]")


def print_local_result(stats: SyncStats) -> None:
    console.print("")
    console.print("[bold]Result[/bold]")
    console.print(f"dirs created : {stats.dirs_created}")
    console.print(f"files written: {stats.files_written}")
    console.print(f"files skipped: {stats.files_skipped}")
    console.print(f"files deleted: {stats.files_deleted}")
    console.print(f"dirs deleted : {stats.dirs_deleted}")
    console.print(f"files failed : {stats.files_failed}")


def print_push_result(stats: SyncStats) -> None:
    console.print("")
    console.print("[bold]Result[/bold]")
    console.print(f"pages created: {stats.pages_created}")
    console.print(f"pages updated: {stats.pages_updated}")
    console.print(f"pages skipped: {stats.pages_skipped}")
    console.print(f"pages deleted: {stats.pages_deleted}")
    console.print(f"paths ignored: {stats.paths_ignored}")
    console.print(f"files failed : {stats.files_failed}")


def exit_if_failed(stats: SyncStats) -> None:
    if stats.files_failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
