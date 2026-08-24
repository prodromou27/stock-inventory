from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Enables PostgreSQL's ltree extension, used by the Location hierarchy (Phase 2).

    Enabled here, ahead of the locations app, per docs/architecture/09-delivery-backlog.md
    Phase 1 checklist ("PostgreSQL service + ltree extension enabled via migration").
    """

    initial = True

    dependencies = []

    operations = [
        CreateExtension("ltree"),
    ]
