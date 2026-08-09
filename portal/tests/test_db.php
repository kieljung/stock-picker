<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';

$pdo = db_connect(':memory:');
migrate($pdo);

$tables = $pdo->query(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
)->fetchAll(PDO::FETCH_COLUMN);

foreach (['bars', 'picks', 'pushes', 'reports', 'schema_version'] as $table) {
    assert_that(in_array($table, $tables, true), "Tabelle $table existiert");
}

assert_equals(1, (int)$pdo->query('SELECT MAX(version) FROM schema_version')->fetchColumn(),
    'Schema steht auf Version 1');

migrate($pdo);
assert_equals(1, (int)$pdo->query('SELECT MAX(version) FROM schema_version')->fetchColumn(),
    'Zweiter Lauf aendert nichts');

exit(test_summary());
