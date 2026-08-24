import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()

# (username, role group, optional env var holding a fixed password)
SEED_USERS = [
    ("devadmin", "Administrator", "SEED_ADMIN_PASSWORD"),
    ("devmanager", "StockManager", "SEED_STOCK_MANAGER_PASSWORD"),
    ("devreadonly", "ReadOnlyUser", "SEED_READONLY_PASSWORD"),
]


class Command(BaseCommand):
    help = (
        "Creates one development user per role (Administrator, StockManager, "
        "ReadOnlyUser). Refuses to run unless DEBUG is enabled."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_dev_data refuses to run with DEBUG=False (production settings)."
            )

        for username, group_name, env_var in SEED_USERS:
            group, _ = Group.objects.get_or_create(name=group_name)
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.invalid"},
            )

            password = os.environ.get(env_var)
            generated = not password
            if generated:
                password = secrets.token_urlsafe(12)

            user.set_password(password)
            user.is_staff = group_name == "Administrator"
            user.is_superuser = False
            user.save()
            user.groups.set([group])

            status = "created" if created else "updated"
            self.stdout.write(f"{status}: {username} ({group_name})")
            if generated:
                self.stdout.write(
                    self.style.WARNING(f"  generated password (not stored): {password}")
                )
