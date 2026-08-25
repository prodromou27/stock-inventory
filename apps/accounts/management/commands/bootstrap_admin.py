import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from apps.accounts.models import MustChangePassword
from apps.core.authorization import ADMINISTRATOR

User = get_user_model()


class Command(BaseCommand):
    """Idempotent single-command install bootstrap (docs/architecture/04-permission-matrix.md's
    "Default admin bootstrap" section) — creates one Administrator account with a
    known default password, forced to change it before doing anything else, so
    `docker compose up` alone (deploy/entrypoint.sh runs this after every
    `migrate`) ends in a working, loggable-in system. Does nothing, safely, on
    every later container start once a real Administrator exists.
    """

    help = (
        "Creates a default Administrator (BOOTSTRAP_ADMIN_USERNAME/PASSWORD, default "
        "admin/admin) that must change its password on first login, unless an "
        "Administrator or superuser already exists. Safe to run on every startup. "
        "Set BOOTSTRAP_ADMIN_ENABLED=false to disable entirely."
    )

    def handle(self, *args, **options):
        if os.environ.get("BOOTSTRAP_ADMIN_ENABLED", "true").strip().lower() in (
            "0",
            "false",
            "no",
        ):
            self.stdout.write("BOOTSTRAP_ADMIN_ENABLED is false — skipping.")
            return

        if (
            User.objects.filter(groups__name=ADMINISTRATOR).exists()
            or User.objects.filter(is_superuser=True).exists()
        ):
            self.stdout.write("An Administrator already exists — skipping bootstrap.")
            return

        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin")
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "admin")

        user, created = User.objects.get_or_create(
            username=username, defaults={"email": f"{username}@example.invalid"}
        )
        user.set_password(password)
        user.save()
        user.groups.add(Group.objects.get(name=ADMINISTRATOR))
        MustChangePassword.objects.get_or_create(user=user)

        self.stdout.write(
            self.style.WARNING(
                f"Created default Administrator '{username}' with a default password — "
                "it must be changed on first login. Log in and change it immediately, "
                "especially before this instance is reachable from an untrusted network."
            )
        )
