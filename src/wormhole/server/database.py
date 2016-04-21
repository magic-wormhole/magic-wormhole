import os, sys
import sqlite3
from pkg_resources import resource_string

class DBError(Exception):
    pass

def get_schema(version):
    schema_bytes = resource_string("wormhole.server",
                                   "db-schemas/v%d.sql" % version)
    return schema_bytes.decode("utf-8")

def get_db(dbfile, stderr=sys.stderr):
    """Open or create the given db file. The parent directory must exist.
    Returns the db connection object, or raises DBError.
    """

    must_create = not os.path.exists(dbfile)
    try:
        db = sqlite3.connect(dbfile)
    except (EnvironmentError, sqlite3.OperationalError) as e:
        raise DBError("Unable to create/open db file %s: %s" % (dbfile, e))
    db.row_factory = sqlite3.Row

    VERSION = 1
    if must_create:
        schema = get_schema(VERSION)
        db.executescript(schema)
        db.execute("INSERT INTO version (version) VALUES (?)", (VERSION,))
        db.commit()

    try:
        version = db.execute("SELECT version FROM version").fetchone()[0]
    except sqlite3.DatabaseError as e:
        # this indicates that the file is not a compatible database format.
        # Perhaps it was created with an old version, or it might be junk.
        raise DBError("db file is unusable: %s" % e)

    if version != VERSION:
        raise DBError("Unable to handle db version %s" % version)

    return db
