"""Minimal Django wrapper around PostgreSQL's `ltree` type (enabled by the
apps.core 0001_enable_ltree migration). ltree defines IMPLICIT casts both ways
between `text` and `ltree`, so passing/receiving plain Python strings works
without a custom psycopg type adapter.

`path` values are computed server-side by a trigger (see
apps/locations/migrations/0002_location_path_trigger.py), never set directly
by application code — this field exists to make the column queryable through
the ORM, not to be written to.
"""

from django.db.models import Field, Lookup


class LtreeField(Field):
    description = "PostgreSQL ltree value"

    def db_type(self, connection):
        return "ltree"

    def get_internal_type(self):
        return "LtreeField"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return str(value)

    def to_python(self, value):
        if value is None:
            return value
        return str(value)

    def get_prep_value(self, value):
        if value is None:
            return value
        return str(value)


class DescendantOrSelf(Lookup):
    """`path__descendant_or_self=<ltree path>` → SQL `path <@ <ltree path>`
    ("is a descendant of, or equal to"). Used by apps.locations.scoping to
    filter to a user's granted subtrees.
    """

    lookup_name = "descendant_or_self"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        params = lhs_params + rhs_params
        return "%s <@ %s" % (lhs, rhs), params


LtreeField.register_lookup(DescendantOrSelf)
