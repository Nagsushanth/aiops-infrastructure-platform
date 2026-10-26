
from getpass import getpass
from sqlalchemy import select

from backend.app.core.dependencies import SessionLocal
from backend.app.core.security import hash_password
from backend.app.models.user import User


def main():
    email = input("Admin email: ").strip().lower()

    if not email or "@" not in email:
        raise SystemExit("Please enter a valid email address.")

    password = getpass("New admin password (12–72 characters): ")

    if not 12 <= len(password) <= 72:
        raise SystemExit("Password must contain 12–72 characters.")

    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))

        if existing:
            print(
                f"Account already exists with role={existing.role}. "
                "No changes made."
            )
            return

        admin = User(
            email=email,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)

        print(f"Development admin created: id={admin.id}, role={admin.role}")


if __name__ == "__main__":
    main()
