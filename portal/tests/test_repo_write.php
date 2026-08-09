<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';
require __DIR__ . '/../htdocs/lib/validate.php';
require __DIR__ . '/../htdocs/lib/repo.php';
require __DIR__ . '/fixtures.php';   // liefert fixture_document()

$pdo = db_connect(':memory:');
migrate($pdo);

$doc = fixture_document();

// Die Fixture ist Grundlage aller weiteren Portal-Tests: faellt sie durch die
// Validierung, scheitern spaeter die Endpunkt-Tests aus einem fremden Grund.
assert_equals([], validate_report($doc), 'Fixture passiert die Validierung');

$result = save_report($pdo, $doc);
assert_equals(false, $result['replaced'], 'Erster Push ersetzt nichts');
assert_equals(2, $result['picks'], 'Zwei Picks geschrieben');
assert_that($result['bars_written'] > 0, 'Bars geschrieben');

$again = save_report($pdo, $doc);
assert_equals(true, $again['replaced'], 'Zweiter Push mit gleichem Datum ersetzt');
assert_equals(1, (int)$pdo->query('SELECT COUNT(*) FROM reports')->fetchColumn(),
    'Weiterhin genau ein Report');
assert_equals(2, (int)$pdo->query('SELECT COUNT(*) FROM picks')->fetchColumn(),
    'Weiterhin genau zwei Picks');

// Status ueberlebt eine Reportkorrektur -- sonst loescht ein Nachtrag die Historie.
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.06,
            triggered_on='2026-08-12', closed_on='2026-08-19' WHERE ticker='XYZ'");
save_report($pdo, $doc);
$kept = $pdo->query("SELECT status, r_multiple, triggered_on, closed_on
                     FROM picks WHERE ticker='XYZ'")->fetch();
assert_equals('TARGET1', $kept['status'], 'Status ueberlebt den erneuten Push');
assert_equals(2.06, (float)$kept['r_multiple'], 'R-Multiple ueberlebt den erneuten Push');
assert_equals('2026-08-12', $kept['triggered_on'], 'triggered_on ueberlebt den erneuten Push');
assert_equals('2026-08-19', $kept['closed_on'], 'closed_on ueberlebt den erneuten Push');

// JSON-Spalten kommen als String in der Datenbank an und wieder heraus.
$stored = $pdo->query("SELECT plan, metrics, score_rationale, sources
                       FROM picks WHERE ticker='ABC'")->fetch();
assert_equals(29.80, json_decode($stored['metrics'], true)['close'],
    'metrics ist als JSON gespeichert');
assert_that(is_array(json_decode($stored['plan'], true)), 'plan ist als JSON gespeichert');
assert_that(is_array(json_decode($stored['score_rationale'], true)),
    'score_rationale ist als JSON gespeichert');
assert_that(is_array(json_decode($stored['sources'], true)),
    'sources ist als JSON gespeichert');

$written = save_bars($pdo, ['XYZ' => [['2026-08-20', 1.0, 2.0, 0.5, 1.5, 100.0]]]);
assert_equals(1, $written, 'Bar geschrieben');
$written = save_bars($pdo, ['XYZ' => [['2026-08-20', 1.0, 2.0, 0.5, 9.9, 100.0]]]);
assert_equals(1, $written, 'Gleiche Bar wird ueberschrieben, nicht dupliziert');
assert_equals(9.9,
    $pdo->query("SELECT close FROM bars WHERE ticker='XYZ' AND date='2026-08-20'")->fetchColumn(),
    'Ueberschriebener Schlusskurs');

$updated = save_outcomes($pdo, [
    ['date' => '2026-08-09', 'ticker' => 'XYZ', 'status' => 'STOP', 'r_multiple' => -1.0],
    ['date' => '2026-08-09', 'ticker' => 'NOPE', 'status' => 'STOP', 'r_multiple' => -1.0],
]);
assert_equals(1, $updated, 'Nur bekannte Picks werden aktualisiert');

log_push($pdo, '/api/report', 1234, 200, 'ok');
assert_equals(1, (int)$pdo->query('SELECT COUNT(*) FROM pushes')->fetchColumn(),
    'Push wird protokolliert');

exit(test_summary());
