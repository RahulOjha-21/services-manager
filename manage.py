#!/usr/bin/env python3
"""(#13) Manage Services Manager user accounts (stored in config/users.yaml).

    python manage.py list-users
    python manage.py add-user <name> --role viewer|operator|admin
    python manage.py passwd   <name>
    python manage.py set-role <name> <role>
    python manage.py del-user <name>

These are the manager's own accounts, independent of Linux/PC users.
"""
import argparse
import getpass
import re
import sys

from app.auth import ROLE_HELP, ROLES, UserStore
from app.config import Config
from app.core.registry import load_config

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")


def store() -> UserStore:
    settings, _ = load_config(Config.SERVICES_FILE, Config.BASE_DIR)
    return UserStore(settings.users_file)


def ask_password(args) -> str:
    if args.password:
        return args.password
    while True:
        p1 = getpass.getpass("New password: ")
        if len(p1) < 8:
            print("  Password must be at least 8 characters.")
            continue
        if p1 != getpass.getpass("Repeat password: "):
            print("  Passwords do not match.")
            continue
        return p1


def main() -> int:
    ap = argparse.ArgumentParser(description="Manage Services Manager users")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users")
    p = sub.add_parser("add-user")
    p.add_argument("name")
    p.add_argument("--role", choices=ROLES, default="viewer")
    p.add_argument("--password", help="non-interactive (avoid: ends up in shell history)")
    p = sub.add_parser("passwd")
    p.add_argument("name")
    p.add_argument("--password")
    p = sub.add_parser("set-role")
    p.add_argument("name")
    p.add_argument("role", choices=ROLES)
    p = sub.add_parser("del-user")
    p.add_argument("name")
    args = ap.parse_args()
    s = store()
    users = s.all()

    if args.cmd == "list-users":
        print(f"Users file: {s.path}")
        if not users:
            print("  (no users)")
        for name, u in sorted(users.items()):
            role = u.get("role", "viewer")
            print(f"  {name:20} {role:9} {ROLE_HELP.get(role, '')}  (created {u.get('created', '?')})")
        return 0

    name = args.name
    if args.cmd == "add-user":
        if not NAME_RE.match(name):
            print("Username: 2-32 characters, letters, digits, . _ -")
            return 1
        if name in users:
            print(f"User '{name}' already exists (use passwd / set-role).")
            return 1
        s.set(name, ask_password(args), args.role)
        print(f"Created '{name}' with role {args.role}.")
        return 0

    if name not in users:
        print(f"No such user '{name}'.")
        return 1
    if args.cmd == "passwd":
        s.set(name, ask_password(args))
        print(f"Password changed for '{name}'.")
    elif args.cmd == "set-role":
        admins = [n for n, u in users.items() if u.get("role") == "admin"]
        if admins == [name] and args.role != "admin":
            print("Refusing: this is the last admin.")
            return 1
        s.set(name, role=args.role)
        print(f"'{name}' is now {args.role}.")
    elif args.cmd == "del-user":
        admins = [n for n, u in users.items() if u.get("role") == "admin"]
        if admins == [name]:
            print("Refusing: this is the last admin.")
            return 1
        s.delete(name)
        print(f"Deleted '{name}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
