"""The ``napper-sync`` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__, categories, famly_mapping, notify, sources
from . import config as cfg
from .famly import FamlyAuthError, FamlyClient, FamlyError, save_token
from .napper import NapperAuthError, NapperClient, NapperError
from .sync import SyncResult, sync


class CliError(Exception):
    """A user-facing error: printed without a traceback, exit code 1."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _baby(args, conf: cfg.Config) -> str:
    baby = getattr(args, "baby", None) or conf.baby_id
    if not baby:
        raise CliError(
            "No baby ID. Pass --baby or set [napper] baby_id in "
            f"{cfg.config_path()} (see `napper-sync babies`)."
        )
    return baby


def _child(args, conf: cfg.Config) -> str:
    child = getattr(args, "child", None) or conf.famly_child_id
    if not child:
        raise CliError(
            "No Famly child ID. Pass --child or set [famly] child_id in "
            f"{cfg.config_path()} (see `napper-sync famly children`)."
        )
    return child


def _parse_fields(pairs: list[str]) -> dict:
    fields = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise CliError(f"--set expects FIELD=VALUE, got '{pair}'")
        key, value = pair.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _summary_title(result: SyncResult) -> str:
    def entries(n: int) -> str:
        return "entry" if n == 1 else "entries"

    if result.written and result.updated:
        return (
            f"{result.written + result.updated} nursery entries: "
            f"{result.written} imported, {result.updated} corrected"
        )
    if result.updated:
        return f"{result.updated} nursery {entries(result.updated)} corrected"
    return f"{result.written} nursery {entries(result.written)} imported"


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_login(args, conf):
    client = NapperClient()
    code = args.code
    if not code:
        client.send_otp(args.email)
        print(f"A one-time code has been sent to {args.email}.")
        try:
            code = input("Code: ").strip()
        except EOFError as err:
            raise CliError("No code entered. Re-run with --code <CODE>.") from err
    tokens = client.email_login(args.email, code)
    print(f"Logged in. Account {tokens.account_id}; tokens stored in {cfg.napper_tokens_path()}")


def cmd_babies(args, conf):
    for baby in NapperClient().babies():
        print(f"{baby['id']}\t{baby.get('name')}")


def cmd_categories(args, conf):
    print(categories.overview())


def cmd_logs(args, conf):
    _dump(NapperClient().logs_between(_baby(args, conf), args.start, args.end))


def cmd_today(args, conf):
    _dump(NapperClient().today(_baby(args, conf)))


def cmd_add(args, conf):
    start = datetime.fromisoformat(args.start).astimezone()
    end = datetime.fromisoformat(args.end).astimezone() if args.end else None
    result = NapperClient().create_log(
        _baby(args, conf),
        args.category,
        start,
        end,
        fields=_parse_fields(args.set),
        comment=args.comment,
        log_id=args.id,
        dry_run=not args.live,
    )
    _dump(result)
    if not args.live:
        print("\nDry run - nothing was written. Pass --live to write.", file=sys.stderr)


def cmd_delete(args, conf):
    _dump(NapperClient().delete_log(_baby(args, conf), args.log_id, dry_run=not args.live))
    if not args.live:
        print("\nDry run - nothing was deleted. Pass --live to delete.", file=sys.stderr)


def cmd_sync_file(args, conf):
    entries = sources.load(Path(args.path), args.format)
    sync(NapperClient(), _baby(args, conf), entries, live=args.live)


def cmd_famly_token(args, conf):
    path = save_token(args.value)
    print(f"Famly token stored in {path}")


def cmd_famly_children(args, conf):
    for child in FamlyClient(host=conf.famly_host).children():
        print(f"{child['id']}\t{child['name']}")


def cmd_famly_calendar(args, conf):
    events = FamlyClient(host=conf.famly_host).calendar(_child(args, conf), args.start, args.end)
    if args.json:
        _dump(events)
        return
    print(f"{len(events)} events")
    for ev in events:
        kind = (ev.get("embed") or {}).get("type") or ("SLEEP" if ev.get("to") else "?")
        print(f"  {ev.get('day')}  {kind:<18} {str(ev.get('title'))[:70]}")


def cmd_sync_famly(args, conf) -> int:
    baby = _baby(args, conf)
    child = _child(args, conf)
    webhook = None if args.no_notify else conf.discord_webhook
    days = args.days if args.days is not None else conf.sync_days
    end = date.today()
    start = end - timedelta(days=days)

    try:
        events = FamlyClient(host=conf.famly_host).calendar(
            child, start.isoformat(), end.isoformat()
        )
    except FamlyAuthError as exc:
        print(f"Famly: {exc}", file=sys.stderr)
        if args.live:
            notify.send(
                webhook,
                "Famly token expired",
                "The sync can no longer reach Famly.\n\n"
                "Get a fresh token: log in to Famly in a browser, copy "
                "`localStorage['famly.accessToken']`, then run\n"
                "```\nnapper-sync famly token <VALUE>\n```\n"
                f"Error:\n```\n{str(exc)[:600]}\n```",
                notify.RED,
            )
        return 1
    except FamlyError as exc:
        print(f"Famly: {exc}", file=sys.stderr)
        if args.live:
            notify.send(webhook, "Famly unreachable", f"```\n{str(exc)[:900]}\n```", notify.YELLOW)
        return 1

    print(f"Famly: {len(events)} events from {start} to {end}")
    entries, notes = famly_mapping.translate(events, marker=conf.marker, child_id=child)

    counts: dict[str, int] = {}
    for e in entries:
        counts[e.category] = counts.get(e.category, 0) + 1
    print("Translated: " + (", ".join(f"{k} x{v}" for k, v in sorted(counts.items())) or "nothing"))
    if notes:
        print(f"Not imported ({len(notes)}):")
        for note in notes[:10]:
            print(f"  {note}")
        if len(notes) > 10:
            print(f"  ... and {len(notes) - 10} more")
    print()

    try:
        result: SyncResult = sync(NapperClient(), baby, entries, live=args.live)
    except (NapperError, categories.CategoryError) as exc:
        print(f"Napper: {exc}", file=sys.stderr)
        if args.live:
            notify.send(webhook, "Napper write failed", f"```\n{str(exc)[:900]}\n```", notify.RED)
        return 1

    if args.live and (result.written or result.updated):
        lines = [f"· {e.label()}" for e in result.new]
        lines += [f"· corrected: {e.label()}  ({'; '.join(diff)})" for e, diff in result.changed]
        notify.send(webhook, _summary_title(result), "\n".join(lines)[:1600], notify.GREEN)
    return 0


def cmd_notify(args, conf):
    if args.unit:
        ok = notify.unit_failed(conf.discord_webhook, args.unit)
    else:
        ok = notify.send(
            conf.discord_webhook,
            "Test message",
            "If you can read this, notifications work.",
            notify.GREEN,
        )
    print("sent" if ok else "failed")
    return 0 if ok else 1


def cmd_config(args, conf):
    path = cfg.config_path()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "init":
        if path.exists() and not args.force:
            raise CliError(f"{path} exists. Use --force to overwrite.")
        cfg.ensure_home()
        path.write_text(cfg.EXAMPLE_CONFIG, encoding="utf-8")
        print(f"Wrote {path} - now fill in the IDs.")
        return 0
    if args.action == "show":
        print(f"# {path}" + ("" if path.exists() else "  (missing - defaults shown)"))
        print(f"baby_id         = {conf.baby_id or ''}")
        print(f"famly.host      = {conf.famly_host}")
        print(f"famly.child_id  = {conf.famly_child_id or ''}")
        print(f"sync.days       = {conf.sync_days}")
        print(f"sync.marker     = {conf.marker}")
        print(f"discord_webhook = {'set' if conf.discord_webhook else 'not set'}")
        print(f"napper tokens   = {'present' if cfg.napper_tokens_path().exists() else 'missing'}")
        print(f"famly token     = {'present' if cfg.famly_token_path().exists() else 'missing'}")
        return 0
    raise CliError(f"Unknown config action {args.action}")


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="napper-sync",
        description="Unofficial Napper client and Famly -> Napper sync.",
        epilog="Writes are dry runs unless --live is given.",
    )
    p.add_argument("--version", action="version", version=f"napper-sync {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="log in to Napper with an e-mailed one-time code")
    s.add_argument("email")
    s.add_argument("--code", help="the code, if you already have it (otherwise prompted)")
    s.set_defaults(fn=cmd_login)

    sub.add_parser("babies", help="list baby profiles and their IDs").set_defaults(fn=cmd_babies)
    sub.add_parser("categories", help="list categories and their fields").set_defaults(
        fn=cmd_categories
    )

    s = sub.add_parser("logs", help="print logs between two days as JSON")
    s.add_argument("start", help="YYYY-MM-DD")
    s.add_argument("end", help="YYYY-MM-DD")
    s.add_argument("--baby", help="baby ID (default: config)")
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("today", help="print today's logs as JSON")
    s.add_argument("--baby")
    s.set_defaults(fn=cmd_today)

    s = sub.add_parser("add", help="add one log entry")
    s.add_argument("category", help="e.g. nap, diaper, bottle - see `categories`")
    s.add_argument("start", help="ISO time, e.g. 2026-09-18T13:05")
    s.add_argument("end", nargs="?", help="ISO time; only for span categories")
    s.add_argument(
        "--set",
        action="append",
        metavar="FIELD=VALUE",
        default=[],
        help="category-specific field, repeatable",
    )
    s.add_argument("--comment")
    s.add_argument("--id", help="overwrite an existing entry instead of creating one")
    s.add_argument("--baby")
    s.add_argument("--live", action="store_true", help="actually write")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("delete", help="delete one log entry")
    s.add_argument("log_id")
    s.add_argument("--baby")
    s.add_argument("--live", action="store_true", help="actually delete")
    s.set_defaults(fn=cmd_delete)

    s = sub.add_parser("sync-file", help="import entries from a CSV or JSON file")
    s.add_argument("path")
    s.add_argument("--format", choices=sorted(sources.SOURCES), help="default: by extension")
    s.add_argument("--baby")
    s.add_argument("--live", action="store_true", help="actually write")
    s.set_defaults(fn=cmd_sync_file)

    f = sub.add_parser("famly", help="Famly side: token, children, calendar")
    fs = f.add_subparsers(dest="famly_cmd", required=True)
    s = fs.add_parser("token", help="store the Famly access token")
    s.add_argument("value")
    s.set_defaults(fn=cmd_famly_token)
    fs.add_parser("children", help="list children and their IDs").set_defaults(
        fn=cmd_famly_children
    )
    s = fs.add_parser("calendar", help="show what the nursery logged")
    s.add_argument("start", help="YYYY-MM-DD")
    s.add_argument("end", help="YYYY-MM-DD")
    s.add_argument("--child")
    s.add_argument("--json", action="store_true", help="raw events as JSON")
    s.set_defaults(fn=cmd_famly_calendar)

    s = sub.add_parser("sync-famly", help="import the nursery's Famly entries into Napper")
    s.add_argument("--baby")
    s.add_argument("--child")
    s.add_argument("--days", type=int, help="look-back window (default: config, 4)")
    s.add_argument("--live", action="store_true", help="actually write")
    s.add_argument("--no-notify", action="store_true", help="skip Discord messages")
    s.set_defaults(fn=cmd_sync_famly)

    s = sub.add_parser("notify", help="send a Discord test message, or a unit-failure report")
    s.add_argument("--unit", help="systemd unit whose journal to attach (used by OnFailure=)")
    s.set_defaults(fn=cmd_notify)

    s = sub.add_parser("config", help="manage the configuration file")
    s.add_argument("action", choices=["init", "show", "path"])
    s.add_argument("--force", action="store_true", help="overwrite an existing config on init")
    s.set_defaults(fn=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s" if args.verbose else "%(message)s",
        stream=sys.stderr,
    )
    conf = cfg.Config.load()
    try:
        code = args.fn(args, conf)
        return int(code or 0)
    except (
        CliError,
        NapperAuthError,
        NapperError,
        FamlyError,
        categories.CategoryError,
        ValueError,
        OSError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
