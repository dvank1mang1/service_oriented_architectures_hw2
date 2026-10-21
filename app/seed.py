import argparse
import getpass
import os

from sqlalchemy import select

from app.db import Session
from app.models import User
from app.security import hasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["USER", "SELLER", "ADMIN"], required=True)
    args = parser.parse_args()
    password = os.environ.get("SEED_PASSWORD") or getpass.getpass("password: ")
    if not 8 <= len(password) <= 128:
        raise SystemExit("password must contain 8 to 128 characters")
    with Session.begin() as session:
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is not None:
            raise SystemExit("account already exists; no changes made")
        user = User(email=args.email.lower(), role=args.role, password_hash=hasher.hash(password))
        session.add(user)
        session.flush()
        print(f"created {user.role}: {user.email} ({user.id})")


if __name__ == "__main__":
    main()
