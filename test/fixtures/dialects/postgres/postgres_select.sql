SELECT timestamp with time zone '2005-04-02 12:00:00-07' + interval '1 day';

-- DATEADD is not a function in postgres so this should parse day as column name
SELECT DATEADD(day, -2, current_date);

SELECT timestamptz '2013-07-01 12:00:00' - timestamptz '2013-03-01 12:00:00';

SELECT 1.0::int;

SELECT '2015-10-24 16:38:46'::TIMESTAMP;

SELECT '2015-10-24 16:38:46'::TIMESTAMP AT TIME ZONE 'UTC';

SELECT '2015-10-24 16:38:46'::TIMESTAMP WITH TIME ZONE;

SELECT '2015-10-24 16:38:46'::TIMESTAMP WITH TIME ZONE AT TIME ZONE 'UTC';

SELECT '2015-10-24 16:38:46'::TIMESTAMP WITHOUT TIME ZONE;

SELECT '2015-10-24 16:38:46'::TIMESTAMPTZ;

SELECT '2015-10-24 16:38:46'::TIMESTAMPTZ AT TIME ZONE 'UTC';

-- Some more example from https://database.guide/how-at-time-zone-works-in-postgresql/

SELECT timestamp with time zone '2025-11-20 00:00:00+00' AT TIME ZONE 'Africa/Cairo';

SELECT timestamp with time zone '2025-11-20 00:00:00';

SELECT timestamp without time zone '2025-11-20 00:00:00' AT TIME ZONE 'Africa/Cairo';

SELECT timestamp without time zone '2025-11-20 00:00:00+12' AT TIME ZONE 'Africa/Cairo';

SELECT timestamp without time zone '2025-11-20 00:00:00+12';

SELECT time with time zone '00:00:00+00' AT TIME ZONE 'Africa/Cairo';

SELECT time without time zone '00:00:00' AT TIME ZONE 'Africa/Cairo';

SELECT c_timestamp AT TIME ZONE 'Africa/Cairo' FROM t_table;

SELECT (c_timestamp AT TIME ZONE 'Africa/Cairo')::time FROM t_table;

SELECT a::double precision FROM my_table;


SELECT
    schema1.table1.columna,
    t.col2
FROM schema1.table1
CROSS JOIN LATERAL somefunc(tb.columnb) as t(col1 text, col2 bool);

SELECT a COLLATE "de_DE" < b FROM test1;

SELECT a < ('foo' COLLATE "fr_FR") FROM test1;

SELECT a < b COLLATE "de_DE" FROM test1;

SELECT a COLLATE "de_DE" < b FROM test1;

SELECT * FROM test1 ORDER BY a || b COLLATE "fr_FR";
