
from collections import namedtuple
from sparktk.inspect import inspect_settings, RowsInspection
from sparktk.row import Row
#from sparktk.rdd import TkRDD
from pyspark.rdd import RDD
from sparktk.dtypes import dtypes
from sparktk.schema import *

TakeResult = namedtuple("TakeResult", ['data', 'schema'])

class _PythonFrame(object):
    def __init__(self, rdd, schema=None):
        self.rdd = rdd
        self.schema = schema

def serialization(sc):
    return sc._jvm.org.trustedanalytics.at.serial.PythonSerialization

def _srdd_to_jrdd(srdd, sc):
    """converts a Scala RDD serialized from Scala usage to a Java RDD serialized for Python RDD usage"""
    return serialization(sc).scalaToPython(srdd)


def _jrdd_to_srdd(jrdd, schema, sc):
    """converts a Java RDD serialized from Python RDD usage to a Scala RDD serialized for Scala RDD usage"""
    return serialization(sc).pythonToScala(jrdd, schema_to_scala(schema, sc))

def _scala_to_python(scala_frame, sc):
    from sparktk import Context
    c = Context(sc)
    print "Scala frame rdd = %s, %s" % (scala_frame.rdd, c.jtypestr(scala_frame.rdd))
    python_rdd = serialization(sc).scalaToPython(scala_frame.rdd)
    python_schema = schema_from_scala(scala_frame.schema, sc)
    return _PythonFrame(python_rdd, python_schema)

def _python_to_scala(python_frame, sc):
    scala_schema = schema_to_scala(python_frame.schema, sc)
    scala_rdd = serialization(sc).pythonToScala(python_frame.rdd._jrdd, scala_schema)
    return _create_scala_frame(sc, scala_rdd, scala_schema)


def _create_scala_frame(sc, scala_rdd, scala_schema):
    return sc._jvm.org.trustedanalytics.at.interfaces.Frame(scala_rdd, scala_schema)


class Frame(object):
    def __init__(self, context, data, schema=None):
        self._context = context
        if self._context.is_scala_rdd(data):
            schema = schema_to_scala(schema, self._context.sc)
            self._frame = self._context.sc._jvm.org.trustedanalytics.at.interfaces.Frame(data, schema)
        else:
            if not isinstance(data, RDD):
                data = self._context.sc.parallelize(data)
            if schema:
                self.validate_pyrdd_schema(data, schema)
            self._frame = _PythonFrame(data, schema)

    def validate_pyrdd_schema(self, pyrdd, schema):
        pass

    @property
    def _is_frame_scala(self):
        return self._context.is_jvm_instance_of(self._frame, self._context.sc._jvm.org.trustedanalytics.at.interfaces.Frame)

    @property
    def _scala(self):
        if not self._is_frame_scala:
            self._frame = _python_to_scala(self._frame, self._context.sc)
        return self._frame

    @property
    def _python(self):
        if self._is_frame_scala:
            rdd = _scala_to_python(self._frame, self._context.sc)
            self._frame = _PythonFrame(rdd, self.schema)
        return self._frame

    def create_scala_frame(self, data, schema):
        return self._context.sc._jvm.org.trustedanalytics.at.interfaces.Frame(data, schema)

    @property
    def schema(self):
        if self._is_frame_scala:
            return schema_from_scala(self._frame.schema(), self._context.sc)  # need ()'s on scala Frame because of def
        return self._frame.schema

    @property
    def rdd(self):
        return self._python.rdd

    def append_csv_file(self, file_name, schema, separator=','):
        self._scala.appendCsvFile(file_name, schema_to_scala(schema, self._context.sc), separator)

    def export_to_csv(self, file_name):
        self._scala.exportToCsv(file_name)

    def count(self):
        #if self._is_frame_scala:
            return int(self._scala.count())
        #print "thus far!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        #return self._python.rdd.count()

    @staticmethod
    def convert_to_python(value, dtype):
        try:
            return dtypes.cast(value, dtype)
        except:
            return None

    def get_row_converter(self, schema):
        row_schema = schema

        def scala_row_to_python(scala_row):
            num_cols = scala_row.length()
            return [self.convert_to_python(scala_row.get(i), row_schema[i][1]) for i in xrange(num_cols)]
        return scala_row_to_python

    def take(self, n):
        if self._is_frame_scala:
            print "take SCALA side!!!!!!!!!!!!!!!!!!!!!"
            scala_data = self._scala.take(n)
            data = map(self.get_row_converter(self.schema), scala_data)
        else:
            data = self._python.rdd.take(n)

        return TakeResult(data=data, schema=self.schema)

    def bin_column(self, column_name, cutoffs):
        #column = self._context.sc._jvm.org.trustedanalytics.at.interfaces.Column()
        self._scala.binColumn4(column_name, cutoffs) #//, True, False, None)

    # @api
    # @has_udf_arg
    # @arg('func', 'UDF', "User-Defined Function (|UDF|) which takes the values in the row and produces a value, or "
    #      "collection of values, for the new cell(s).")
    # @arg('schema', 'tuple | list of tuples', "The schema for the results of the |UDF|, indicating the new column(s) to "
    #      "add.  Each tuple provides the column name and data type, and is of the form (str, type).")
    # @arg('columns_accessed', list, "List of columns which the |UDF| will access.  This adds significant performance "
    #      "benefit if we know which column(s) will be needed to execute the |UDF|, especially when the frame has "
    #      "significantly more columns than those being used to evaluate the |UDF|.")
    def add_columns(self, func, schema, columns_accessed=None):
        """
        Add columns to current frame.

        Assigns data to column based on evaluating a function for each row.

        Notes
        -----
        1)  The row |UDF| ('func') must return a value in the same format as
            specified by the schema.
            See :doc:`/ds_apir`.
        2)  Unicode in column names is not supported and will likely cause the
            drop_frames() method (and others) to fail!

        Examples
        --------
        Given our frame, let's add a column which has how many years the person has been over 18

        .. code::

            >>> frame.inspect()
            [#]  name      age  tenure  phone
            ====================================
            [0]  Fred       39      16  555-1234
            [1]  Susan      33       3  555-0202
            [2]  Thurston   65      26  555-4510
            [3]  Judy       44      14  555-2183

            >>> frame.add_columns(lambda row: row.age - 18, ('adult_years', ta.int32))
            <progress>

            >>> frame.inspect()
            [#]  name      age  tenure  phone     adult_years
            =================================================
            [0]  Fred       39      16  555-1234           21
            [1]  Susan      33       3  555-0202           15
            [2]  Thurston   65      26  555-4510           47
            [3]  Judy       44      14  555-2183           26


        Multiple columns can be added at the same time.  Let's add percentage of
        life and percentage of adult life in one call, which is more efficient.

        .. code::

            >>> frame.add_columns(lambda row: [row.tenure / float(row.age), row.tenure / float(row.adult_years)], [("of_age", ta.float32), ("of_adult", ta.float32)])
            <progress>
            >>> frame.inspect(round=2)
            [#]  name      age  tenure  phone     adult_years  of_age  of_adult
            ===================================================================
            [0]  Fred       39      16  555-1234           21    0.41      0.76
            [1]  Susan      33       3  555-0202           15    0.09      0.20
            [2]  Thurston   65      26  555-4510           47    0.40      0.55
            [3]  Judy       44      14  555-2183           26    0.32      0.54

        Note that the function returns a list, and therefore the schema also needs to be a list.

        It is not necessary to use lambda syntax, any function will do, as long as it takes a single row argument.  We
        can also call other local functions within.

        Let's add a column which shows the amount of person's name based on their adult tenure percentage.

            >>> def percentage_of_string(string, percentage):
            ...     '''returns a substring of the given string according to the given percentage'''
            ...     substring_len = int(percentage * len(string))
            ...     return string[:substring_len]

            >>> def add_name_by_adult_tenure(row):
            ...     return percentage_of_string(row.name, row.of_adult)

            >>> frame.add_columns(add_name_by_adult_tenure, ('tenured_name', unicode))
            <progress>

            >>> frame
            Frame "example_frame"
            row_count = 4
            schema = [name:unicode, age:int32, tenure:int32, phone:unicode, adult_years:int32, of_age:float32, of_adult:float32, tenured_name:unicode]
            status = ACTIVE  (last_read_date = -etc-)

            >>> frame.inspect(columns=['name', 'of_adult', 'tenured_name'], round=2)
            [#]  name      of_adult  tenured_name
            =====================================
            [0]  Fred          0.76  Fre
            [1]  Susan         0.20  S
            [2]  Thurston      0.55  Thur
            [3]  Judy          0.54  Ju


        **Optimization** - If we know up front which columns our row function will access, we
        can tell add_columns to speed up the execution by working on only the limited feature
        set rather than the entire row.

        Let's add a name based on tenure percentage of age.  We know we're only going to use
        columns 'name' and 'of_age'.

        .. code::

            >>> frame.add_columns(lambda row: percentage_of_string(row.name, row.of_age),
            ...                   ('tenured_name_age', unicode),
            ...                   columns_accessed=['name', 'of_age'])
            <progress>
            >>> frame.inspect(round=2)
            [#]  name      age  tenure  phone     adult_years  of_age  of_adult
            ===================================================================
            [0]  Fred       39      16  555-1234           21    0.41      0.76
            [1]  Susan      33       3  555-0202           15    0.09      0.20
            [2]  Thurston   65      26  555-4510           47    0.40      0.55
            [3]  Judy       44      14  555-2183           26    0.32      0.54
            <blankline>
            [#]  tenured_name  tenured_name_age
            ===================================
            [0]  Fre           F
            [1]  S
            [2]  Thur          Thu
            [3]  Ju            J

        More information on a row |UDF| can be found at :doc:`/ds_apir`

        """
        # For further examples, see :ref:`example_frame.add_columns`.
        #self._backend.add_columns(self, func, schema, columns_accessed)
        row = Row(self.schema)
        def add_columns_func(r):
            row._set_data(r)
            return func(row)
        if isinstance(schema, list):
            self._rdd = self.rdd.map(lambda r: r + add_columns_func(r))
            self.schema.extend(schema)
        else:
            self._rdd = self.rdd.map(lambda r: r + [add_columns_func(r)])
            self.schema.append(schema)

    # @api
    # @has_udf_arg
    # @arg('predicate', 'function', "|UDF| which evaluates a row to a boolean; rows that answer True are dropped from the Frame")
    def __drop_rows(self, predicate):
        """
        Erase any row in the current frame which qualifies.

        Examples
        --------

        .. code::

            <hide>
            >>> frame = _frame.copy()
            <progress>

            </hide>

            >>> frame.inspect()
            [#]  name      age  tenure  phone
            ====================================
            [0]  Fred       39      16  555-1234
            [1]  Susan      33       3  555-0202
            [2]  Thurston   65      26  555-4510
            [3]  Judy       44      14  555-2183
            >>> frame.drop_rows(lambda row: row.name[-1] == 'n')  # drop people whose name ends in 'n'
            <progress>
            >>> frame.inspect()
            [#]  name  age  tenure  phone
            ================================
            [0]  Fred   39      16  555-1234
            [1]  Judy   44      14  555-2183

        More information on a |UDF| can be found at :doc:`/ds_apir`.
        """
        row = Row(self.schema)
        def drop_rows_func(r):
            row._set_data(r)
            return not predicate(row)
        self._rdd = self.rdd.filter(drop_rows_func)

    # @api
    # @has_udf_arg
    # @arg('predicate', 'function', "|UDF| which evaluates a row to a boolean; rows that answer False are dropped from the Frame")
    def filter(self, predicate):
        """
        Select all rows which satisfy a predicate.

        Modifies the current frame to save defined rows and delete everything
        else.

        Examples
        --------
            <hide>
            >>> frame = _frame.copy()
            <progress>

            </hide>

            >>> frame.inspect()
            [#]  name      age  tenure  phone
            ====================================
            [0]  Fred       39      16  555-1234
            [1]  Susan      33       3  555-0202
            [2]  Thurston   65      26  555-4510
            [3]  Judy       44      14  555-2183
            >>> frame.filter(lambda row: row.tenure >= 15)  # keep only people with 15 or more years tenure
            <progress>
            >>> frame.inspect()
            [#]  name      age  tenure  phone
            ====================================
            [0]  Fred       39      16  555-1234
            [1]  Thurston   65      26  555-4510

        More information on a |UDF| can be found at :doc:`/ds_apir`.
        """
        row = Row(self.schema)
        def filter_func(r):
            row._set_data(r)
            return predicate(row)
        self._rdd = self.rdd.filter(filter_func)


    # @api
    # @arg('n', int, 'The number of rows to print (warning: do not overwhelm this client by downloading too much)')
    # @arg('offset', int, 'The number of rows to skip before printing.')
    # @arg('columns', int, 'Filter columns to be included.  By default, all columns are included')
    # @arg('wrap', "int or 'stripes'", "If set to 'stripes' then inspect prints rows in stripes; if set to an integer N, "
    #                                  "rows will be printed in clumps of N columns, where the columns are wrapped")
    # @arg('truncate', int, 'If set to integer N, all strings will be truncated to length N, including a tagged ellipses')
    # @arg('round', int, 'If set to integer N, all floating point numbers will be rounded and truncated to N digits')
    # @arg('width', int, 'If set to integer N, the print out will try to honor a max line width of N')
    # @arg('margin', int, "('stripes' mode only) If set to integer N, the margin for printing names in a "
    #                     "stripe will be limited to N characters")
    # @arg('with_types', bool, "If set to True, header will include the data_type of each column")
    # @returns('RowsInspection', "An object which naturally converts to a pretty-print string")
    def inspect(self,
                n=10,
                offset=0,
                columns=None,
                wrap=inspect_settings._unspecified,
                truncate=inspect_settings._unspecified,
                round=inspect_settings._unspecified,
                width=inspect_settings._unspecified,
                margin=inspect_settings._unspecified,
                with_types=inspect_settings._unspecified):
        """
        Pretty-print of the frame data

        Essentially returns a string, but technically returns a RowInspection object which renders a string.
        The RowInspection object naturally converts to a str when needed, like when printed or when displayed
        by python REPL (i.e. using the object's __repr__).  If running in a script and want the inspect output
        to be printed, then it must be explicitly printed, then `print frame.inspect()`


        Examples
        --------
        To look at the first 4 rows of data in a frame:

        .. code::

        <skip>
            >>> frame.inspect(4)
            [#]  animal    name    age  weight
            ==================================
            [0]  human     George    8   542.5
            [1]  human     Ursula    6   495.0
            [2]  ape       Ape      41   400.0
            [3]  elephant  Shep      5  8630.0
        </skip>

        # For other examples, see :ref:`example_frame.inspect`.

        Note: if the frame data contains unicode characters, this method may raise a Unicode exception when
        running in an interactive REPL or otherwise which triggers the standard python repr().  To get around
        this problem, explicitly print the unicode of the returned object:

        .. code::

        <skip>
            >>> print unicode(frame.inspect())
        </skip>


        **Global Settings**

        If not specified, the arguments that control formatting receive default values from
        'trustedanalytics.inspect_settings'.  Make changes there to affect all calls to inspect.

        .. code::

            >>> import trustedanalytics as ta
            >>> ta.inspect_settings
            wrap             20
            truncate       None
            round          None
            width            80
            margin         None
            with_types    False
            >>> ta.inspect_settings.width = 120  # changes inspect to use 120 width globally
            >>> ta.inspect_settings.truncate = 16  # changes inspect to always truncate strings to 16 chars
            >>> ta.inspect_settings
            wrap             20
            truncate         16
            round          None
            width           120
            margin         None
            with_types    False
            >>> ta.inspect_settings.width = None  # return value back to default
            >>> ta.inspect_settings
            wrap             20
            truncate         16
            round          None
            width            80
            margin         None
            with_types    False
            >>> ta.inspect_settings.reset()  # set everything back to default
            >>> ta.inspect_settings
            wrap             20
            truncate       None
            round          None
            width            80
            margin         None
            with_types    False

        ..
        """
        format_settings = inspect_settings.copy(wrap, truncate, round, width, margin, with_types)
        result = self.take(n) #, offset, selected_columns)
        data = result.data
        schema = result.schema
        return RowsInspection(data, schema, offset=offset, format_settings=format_settings)