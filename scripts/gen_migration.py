"""Generate Alembic migration from SQLAlchemy metadata, no DB required.

Run: uv run python scripts/gen_migration.py
"""
from __future__ import annotations

import pathlib
import sys

# ensure project root is on path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import enum

# Re-import models to populate Base.metadata
from models import Base  # noqa: F401


def gen_migration() -> str:
    metadata = Base.metadata
    dialect = postgresql.dialect()

    lines: list[str] = []
    lines.append('"""init: users, chats, chat_members, tv_devices, chat_tv_bindings')
    lines.append('')
    lines.append('Revision ID: 0001_init')
    lines.append('Revises:')
    lines.append('Create Date: 2026-09-07')
    lines.append('')
    lines.append('"""')
    lines.append('from __future__ import annotations')
    lines.append('')
    lines.append('from collections.abc import Sequence')
    lines.append('')
    lines.append('from alembic import op')
    lines.append('import sqlalchemy as sa')
    lines.append('')
    lines.append('revision: str = "0001_init"')
    lines.append('down_revision: str | None = None')
    lines.append('branch_labels: str | Sequence[str] | None = None')
    lines.append('depends_on: str | Sequence[str] | None = None')
    lines.append('')
    lines.append('')
    lines.append('def upgrade() -> None:')
    lines.append('    # Enums')
    lines.append('    user_role = sa.Enum("member", "admin", "root", name="user_role")')
    lines.append('    user_role.create(op.get_bind(), checkfirst=True)')
    lines.append('    tv_device_status = sa.Enum("pending", "online", "offline", "disabled", name="tv_device_status")')
    lines.append('    tv_device_status.create(op.get_bind(), checkfirst=True)')
    lines.append('    chat_tv_binding_status = sa.Enum("active", "archived", name="chat_tv_binding_status")')
    lines.append('    chat_tv_binding_status.create(op.get_bind(), checkfirst=True)')
    lines.append('')

    def sa_type(col) -> str:
        t = col.type
        cls = t.__class__.__name__
        if cls == "Enum":
            # Use lowercase values from the Python enum
            from sqlalchemy import Enum as SAEnum
            python_enum = t.enum_class
            if python_enum is not None:
                values = ", ".join(f'"{e.value}"' for e in python_enum)
            else:
                values = ", ".join(f'"{v}"' for v in t.enums)
            return f'sa.Enum({values}, name="{t.name}")'
        if cls == "Integer":
            return "sa.Integer()"
        if cls == "BigInteger":
            return "sa.BigInteger()"
        if cls == "String":
            return f"sa.String(length={t.length})"
        if cls == "Boolean":
            return "sa.Boolean()"
        if cls == "DateTime":
            return "sa.DateTime(timezone=True)"
        if cls == "Text":
            return "sa.Text()"
        return f"sa.{cls}()"

    for table in metadata.sorted_tables:
        lines.append(f'    op.create_table(')
        lines.append(f'        "{table.name}",')
        for col in table.columns:
            nullable = ", nullable=True" if col.nullable else ", nullable=False"
            pk = ", primary_key=True" if col.primary_key else ""
            unique = ", unique=True" if col.unique else ""
            default = ""
            if col.server_default is not None:
                arg = col.server_default.arg
                if hasattr(arg, "value"):
                    val = str(arg.value)
                    default = f", server_default=sa.text({val!r})"
                elif isinstance(arg, str):
                    # raw SQL fragment — wrap in sa.text to avoid quoting issues
                    default = f", server_default=sa.text({arg!r})"
            lines.append(f'        sa.Column("{col.name}", {sa_type(col)}{nullable}{pk}{unique}{default}),')
        for fk in table.foreign_keys:
            ref = fk.target_fullname
            ondelete_clause = ""
            # Find ondelete via inspection
            for constraint in table.constraints:
                if (
                    hasattr(constraint, "elements")
                    and any(e.parent.name == fk.parent.name for e in constraint.elements)
                    and constraint.ondelete is not None
                ):
                    ondelete_clause = f", ondelete={constraint.ondelete!r}"
            lines.append(f'        sa.ForeignKeyConstraint(["{fk.parent.name}"], ["{ref}"]{ondelete_clause}),')
        lines.append(f'    )')
        for uc in table.constraints:
            if uc.__class__.__name__ == "UniqueConstraint":
                cols = ", ".join(f'"{c.name}"' for c in uc.columns)
                lines.append(
                    f'    op.create_unique_constraint("{uc.name}", "{table.name}", [{cols}])'
                )
        for idx in table.indexes:
            if idx.unique:
                continue
            cols = ", ".join(f'"{c.name}"' for c in idx.columns)
            lines.append(f'    op.create_index("{idx.name}", "{table.name}", [{cols}])')
        lines.append('')

    lines.append('')
    lines.append('def downgrade() -> None:')
    for table in reversed(list(metadata.sorted_tables)):
        lines.append(f'    op.drop_table("{table.name}")')
    lines.append('    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)')
    lines.append('    sa.Enum(name="tv_device_status").drop(op.get_bind(), checkfirst=True)')
    lines.append('    sa.Enum(name="chat_tv_binding_status").drop(op.get_bind(), checkfirst=True)')

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out = pathlib.Path("migrations/versions/0001_init.py")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gen_migration())
    print(f"Wrote {out}")
