from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Enables PostgreSQL's pg_trgm extension — trigram similarity powering
    typo-tolerant ranking in the global search feature (apps.core.views.
    GlobalSearchView) and the GIN trigram indexes added alongside it in
    apps.catalog/apps.inventory. Same enable-ahead-of-use pattern as
    0001_enable_ltree.py.
    """

    dependencies = [
        ("core", "0001_enable_ltree"),
    ]

    operations = [
        TrigramExtension(),
    ]
