#!/usr/bin/env python3
"""
Generic database connector class for PostgresQL databases. It includes a built-in logging system (logger)

author: Enoc Martínez
institution: Universitat Politècnica de Catalunya (UPC)
email: enoc.martinez@upc.edu
license: MIT
created: 4/10/23
"""

from ..common import LoggerSuperclass, PRL
import psycopg2
import time
import pandas as pd
import traceback
import rich


class Connection(LoggerSuperclass):
    def __init__(self, host, port, db_name, db_user, db_password, timeout, logger, count, autocommit=False):
        LoggerSuperclass.__init__(self, logger, f"PGCON{count}-{db_name}", colour=PRL)
        self.info("Creating connection")
        self.__host = host
        self.__port = port
        self.__name = db_name
        self.__user = db_user
        self.__pwd = db_password
        self.__timeout = timeout

        self.__connection_count = count

        self.available = True  # flag that determines if this connection is available or not
        # Create a connection
        self.connection = psycopg2.connect(host=self.__host, port=self.__port, dbname=self.__name, user=self.__user,
                                           password=self.__pwd, connect_timeout=self.__timeout)
        if autocommit:
            self.connection.autocommit = autocommit

        self.cursor = self.connection.cursor()
        self.last_used = -1

        self.index = 0
        self.__closing = False

    def run_query(self, query, description=False, debug=False, fetch=True):
        """
        Executes a query and returns the result. If description=True the desription will also be returned
        """
        self.available = False
        if debug:
            self.debug(query)

        if type(query) is tuple:
            # If tuple assume that it has two parts
            sql_query, data = query
            self.cursor.execute(sql_query, data)
        else:
            self.cursor.execute(query)

        self.connection.commit()
        if fetch:
            resp = self.cursor.fetchall()
            self.available = True
            if description:
                return resp, self.cursor.description
            return resp
        else:
            self.available = True
            return

    def close(self):
        if not self.__closing:
            self.__closing = True
            self.info(f"Closing connection")
            self.connection.close()
        else:
            self.error(f"Someone else is closing connection {self.__connection_count}!")


class PgDatabaseConnector(LoggerSuperclass):
    """
    Interface to access a PostgresQL database
    """

    def __init__(self, host, port, db_name, db_user, db_password, logger, timeout=5, autocommit=False):
        LoggerSuperclass.__init__(self, logger, "PostgresQL")
        self.conn_count = 0
        self.__host = host
        self.__port = port
        self.__name = db_name
        self.__user = db_user
        self.__pwd = db_password
        self.__timeout = timeout
        self.__logger = logger
        self.autocommit = autocommit

        self.query_time = -1  # stores here the execution time of the last query
        self.db_initialized = False
        self.connections = []  # list of connections, starts with one
        self.max_connections = 50

        # Check for the constraints
        self.get_available_connection()

    def new_connection(self) -> Connection:
        self.conn_count += 1
        c = Connection(self.__host, self.__port, self.__name, self.__user, self.__pwd, self.__timeout, self.__logger,
                       self.conn_count, autocommit=self.autocommit)
        self.connections.append(c)
        return c

    def close(self):
        for i in range(len(self.connections)):
            self.info(f"Closing connectino {i}")
            c = self.connections[i]
            c.close()

    def get_available_connection(self):
        """
        Loops through the connections and gets the first available. If there isn't any available create a new one (or
        wait if connections reached the limit).
        """

        for i in range(len(self.connections)):
            c = self.connections[i]
            if c.available:
                return c

        while len(self.connections) >= self.max_connections:
            time.sleep(0.5)
            self.debug("waiting for conn")

        self.info(f"Creating DB connection to  {self.__name}..")
        return self.new_connection()

    def exec_query(self, query, description=False, debug=False, fetch=True, ignore_errors=False):
        """
        Runs a query in a free connection
        """
        c = self.get_available_connection()
        results = None
        try:
            results = c.run_query(query, description=description, debug=debug, fetch=fetch)

        except psycopg2.errors.UniqueViolation as e:
            # most likely a duplicated key, raise it again
            c.connection.rollback()
            c.available = True  # set it to available
            self.info(f"Query: {query}")
            self.error(f"Exception caught!:\n{traceback.format_exc()}")
            raise e

        except Exception as e:
            self.warning(f"Exception caught!:\n{traceback.format_exc()}")
            self.info(f"Query: {query}")
            self.error(f"Exception in exec_query {e}")

            if not ignore_errors:
                raise e

            try:
                self.warning("closing db connection due to exception")
                c.close()
            except Exception as e:  # ignore errors
                if not ignore_errors:
                    raise e
                else:
                    pass
            self.error(f"Removing connection")
            self.connections.remove(c)
        return results

    def value_from_query(self, query, debug=False):
        """
        Run a single value from a query
        """
        response = self.exec_query(query, debug=debug, fetch=True)
        if len(response) != 1:
            self.warning(f"query: {query}")
            raise LookupError(f"Expected only one column, got {len(response)}")
        elif len(response[0]) != 1:
            self.warning(f"query: {query}")
            raise LookupError(f"Expected one value, got {len(response)}")
        return response[0][0]

    def list_from_query(self, query, debug=False):
        """
        Makes a query to the database using a cursor object and returns a DataFrame object
        with the reponse
        :param query: string with the query
        :param debug:
        :returns list with the query result
        """
        r = self.exec_query(query, debug=debug)

        # Avoid to have a list of tuple like [(2,),(3,)], converting to [2,3]
        if len(r) > 0 and len(r[0]) == 1:
            r = [e[0] for e in r]
        return r

    def dataframe_from_query(self, query, debug=False):
        """
        Makes a query to the database using a cursor object and returns a DataFrame object
        with the reponse
        :param cursor: database cursor
        :param query: string with the query
        :param debug:
        :returns DataFrame with the query result
        """
        response, description = self.exec_query(query, debug=debug, description=True)
        colnames = [desc[0] for desc in description]  # Get the Column names
        df = pd.DataFrame(response, columns=colnames)
        return df

    def close(self):
        for c in self.connections:
            c.close()

    def add_constraint(self, constraint_name, query):
        """
        Checks if a constraint is already present in pg_constraint table. If not, create it using the query
        """
        pg_constraints = self.list_from_query(f"select conname from pg_constraint;")
        if constraint_name not in pg_constraints:
            self.exec_query(query, fetch=False)

    def add_index(self, index_name, table_name, query):

        pg_indexes = self.list_from_query(f"select indexname from pg_indexes where tablename = '{table_name}'")
        if index_name not in pg_indexes:
            self.exec_query(query, fetch=False)

    def check_if_table_exists(self, view_name):
        """
        Checks if a view already exists
        :param view_name: database view to check if exists
        :return: True if exists, False if it doesn't
        """
        # Select all from information_schema
        query = "SELECT table_name FROM information_schema.tables"
        df = self.dataframe_from_query(query)
        table_names = df["table_name"].values
        if view_name in table_names:
            return True
        return False

    def check_if_database_exists(self, dbname) -> bool:
        """
        Checks if a database exists
        :return: True/False
        """
        databases = self.list_from_query("SELECT datname FROM pg_database WHERE datistemplate = false;")
        if dbname in databases:
            return True
        return False
