"""
Create a user account (typically the first admin) from the command line.
There's no self-service signup by design -- accounts for this tool are
provisioned by whoever administers it.

Usage (from the repo root):

    PYTHONPATH=. python3 -m backend.create_user --username alice --role admin
    PYTHONPATH=. python3 -m backend.create_user --username bob --role member

Prompts for a password interactively (not passed as a CLI arg, so it
doesn't end up in shell history or process listings).
"""
import argparse
import getpass
import sys

from backend.auth.service import AuthService, UsernameTakenError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=["admin", "member"], default="member")
    parser.add_argument("--db-path", default="backend/data/logs.db")
    args = parser.parse_args()

    password = getpass.getpass(f"Password for '{args.username}': ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 1
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1

    auth = AuthService(db_path=args.db_path)
    try:
        user = auth.create_user(args.username, password, role=args.role)
    except UsernameTakenError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Created user '{user.username}' with role '{user.role}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
