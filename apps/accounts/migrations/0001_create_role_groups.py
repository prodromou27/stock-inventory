from django.db import migrations

# Role is expressed as Django Group membership (docs/architecture/04-permission-matrix.md),
# not a custom field — a user has exactly one of these groups.
ROLE_GROUPS = ["Administrator", "StockManager", "ReadOnlyUser"]


def create_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ROLE_GROUPS:
        Group.objects.get_or_create(name=name)


def remove_role_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLE_GROUPS).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_role_groups, remove_role_groups),
    ]
