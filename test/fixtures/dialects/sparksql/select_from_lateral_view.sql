SELECT
    id,
    name,
    age,
    class,
    address,
    c_age,
    d_age
FROM person
    LATERAL VIEW EXPLODE(ARRAY(30, 60)) tbl_name AS c_age
    LATERAL VIEW EXPLODE(ARRAY(40, 80)) AS d_age;

SELECT
    c_age,
    COUNT(*) AS record_count
FROM person
    LATERAL VIEW EXPLODE(ARRAY(30, 60)) AS c_age
    LATERAL VIEW EXPLODE(ARRAY(40, 80)) AS d_age
GROUP BY c_age;

SELECT
    id,
    name,
    age,
    class,
    address,
    c_age,
    d_age
FROM person
    LATERAL VIEW EXPLODE(ARRAY()) tbl_name AS c_age;

SELECT
    id,
    name,
    age,
    class,
    address,
    c_age
FROM person
    LATERAL VIEW OUTER EXPLODE(ARRAY()) tbl_name AS c_age;

SELECT
    person.id,
    exploded_people.name,
    exploded_people.age,
    exploded_people.state
FROM person
    LATERAL VIEW INLINE(array_of_structs) exploded_people AS name, age, state;

SELECT
    p.id,
    exploded_people.name,
    exploded_people.age,
    exploded_people.state
FROM person AS p
    LATERAL VIEW INLINE(array_of_structs) exploded_people AS name, age, state;
