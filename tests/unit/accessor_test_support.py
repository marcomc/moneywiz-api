import sqlite3

from moneywiz_api.database_accessor import DatabaseAccessor


def initialized_memory_accessor(rows, entity_metadata):
    """Build a guarded accessor after assembling its complete synthetic schema."""
    connection = sqlite3.connect(":memory:")

    def dict_factory(cursor, values):
        return {
            description[0]: values[index]
            for index, description in enumerate(cursor.description)
        }

    connection.row_factory = dict_factory
    connection.execute(
        "CREATE TABLE Z_PRIMARYKEY "
        "(Z_ENT INTEGER, Z_NAME TEXT, Z_SUPER INTEGER, Z_MAX INTEGER)"
    )
    connection.executemany(
        "INSERT INTO Z_PRIMARYKEY VALUES (?, ?, ?, 0)", entity_metadata
    )
    columns = tuple(dict.fromkeys(key for row in rows for key in row))
    definitions = ", ".join(f'"{column}"' for column in columns)
    connection.execute(f"CREATE TABLE ZSYNCOBJECT ({definitions})")
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO ZSYNCOBJECT VALUES ({placeholders})",
        [tuple(row.get(column) for column in columns) for row in rows],
    )
    connection.commit()

    accessor = DatabaseAccessor.__new__(DatabaseAccessor)
    accessor._con = connection
    accessor._initialize_schema_cache()
    return accessor
