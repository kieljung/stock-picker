<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/validate.php';

function good_pick(array $overrides = []): array {
    return array_merge([
        'ticker' => 'XYZ', 'name' => 'Example', 'sector' => 'Health Care',
        'setup' => 'VCP_BREAKOUT', 'rank' => 1,
        'score_technical' => 8, 'score_catalyst' => 7, 'score_risk' => 7,
        'composite' => 76,
        'score_rationale' => ['technical' => 'a', 'catalyst' => 'b', 'risk' => 'c'],
        'entry' => 42.80, 'stop' => 40.10, 'target1' => 48.20, 'target2' => 52.00,
        'catalyst' => 'FDA', 'thesis' => 't', 'technicals' => 'tl',
        'catalyst_detail' => 'kd',
        'plan' => ['entry' => 'e', 'stop' => 's', 'target1' => 't1', 'target2' => 't2'],
        'bear_case' => 'bc', 'invalidation' => 'inv', 'metrics' => [],
        'sources' => [['url' => 'https://example.com/a']],
    ], $overrides);
}

function good_doc(array $overrides = []): array {
    return array_merge([
        'date' => '2026-08-09', 'generated_at' => '2026-08-09T12:35:34',
        'market_context' => 'mk', 'picks' => [good_pick()],
    ], $overrides);
}

/** Ein Pick ohne das genannte Feld -- array_merge kann nichts entfernen. */
function pick_without(string $field): array {
    $pick = good_pick();
    unset($pick[$field]);
    return $pick;
}

assert_equals([], validate_report(good_doc()), 'Gueltiges Dokument passiert');

$errors = validate_report(good_doc(['date' => '9.8.2026']));
assert_that(count($errors) > 0, 'Datum ohne ISO-Format wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['stop' => 43.5])]]));
assert_that(str_contains(implode(' ', $errors), 'Stop'), 'Stop ueber Entry wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['target1' => 45.0])]]));
assert_that(str_contains(implode(' ', $errors), 'R:R'), 'R:R unter 2 wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['composite' => 55])]]));
assert_that(str_contains(implode(' ', $errors), 'Composite'), 'Composite unter 60 wird abgelehnt');

$errors = validate_report(good_doc(['picks' => array_fill(0, 6, good_pick())]));
assert_that(str_contains(implode(' ', $errors), '5'), 'Mehr als fuenf Picks werden abgelehnt');

$three = [good_pick(['ticker' => 'A']), good_pick(['ticker' => 'B']), good_pick(['ticker' => 'C'])];
$errors = validate_report(good_doc(['picks' => $three]));
assert_that(str_contains(implode(' ', $errors), 'Sektor'), 'Drei Titel je Sektor werden abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['bear_case' => '  '])]]));
assert_that(str_contains(implode(' ', $errors), 'bear_case'), 'Leerer Bear-Case wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [
    good_pick(['sources' => [['url' => 'javascript:alert(1)']]])]]));
assert_that(str_contains(implode(' ', $errors), 'sources'), 'Nicht-HTTP-Quelle wird abgelehnt');

// Die folgenden Regeln stehen in stockpicker/report.py und fehlten in der
// Plan-Vorlage: das Portal rendert diese Felder, ein fehlendes faellt erst
// auf der Seite auf, wenn es zu spaet ist.
$errors = validate_report(good_doc(['picks' => [pick_without('name')]]));
assert_that(str_contains(implode(' ', $errors), "'name'"), 'Fehlendes Pflichtfeld wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick(['metrics' => []])]]));
assert_equals([], $errors, 'Leeres metrics-Objekt ist erlaubt, solange der Key da ist');

$errors = validate_report(good_doc(['picks' => [
    good_pick(['score_rationale' => ['technical' => 'a', 'catalyst' => 'b']])]]));
assert_that(str_contains(implode(' ', $errors), 'score_rationale.risk'),
    'Unvollstaendige score_rationale wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [
    good_pick(['plan' => ['entry' => 'e', 'stop' => 's', 'target1' => 't1']])]]));
assert_that(str_contains(implode(' ', $errors), 'plan.target2'),
    'Unvollstaendiger Plan wird abgelehnt');

$errors = validate_report(good_doc(['picks' => ['kein Objekt']]));
assert_that(str_contains(implode(' ', $errors), 'kein Objekt'),
    'Pick, der kein Objekt ist, wird abgelehnt statt fatal');

// Der Abgleich des Entrys gegen den letzten Schlusskurs, gespiegelt aus
// stockpicker/ledger.py. Die Bars kommen aus yfinance, nicht aus dem Modell.
function bars_for(array $rows): array {
    return ['XYZ' => $rows];
}

$bars = bars_for([
    ['2026-08-06', 42.0, 42.9, 41.7, 42.50, 1000.0],
    ['2026-08-07', 42.3, 43.1, 42.0, 42.75, 1100.0],
]);
assert_equals([], validate_report(good_doc(), $bars),
    'Entry nahe am letzten Schlusskurs passiert');

$errors = validate_report(good_doc(['picks' => [good_pick([
    'entry' => 90.00, 'stop' => 85.00, 'target1' => 101.00, 'target2' => 110.00])]]), $bars);
assert_that(str_contains(implode(' ', $errors), 'halluziniert'),
    'Entry weit ueber dem letzten Schlusskurs wird abgelehnt');

$errors = validate_report(good_doc(['picks' => [good_pick([
    'entry' => 90.00, 'stop' => 85.00, 'target1' => 101.00, 'target2' => 110.00])]]),
    ['ABC' => $bars['XYZ']]);
assert_equals([], $errors, 'Ohne Bars zum Ticker wird die Pruefung uebersprungen');

$errors = validate_report(good_doc(['picks' => [good_pick([
    'entry' => 90.00, 'stop' => 85.00, 'target1' => 101.00, 'target2' => 110.00])]]),
    bars_for([['2026-08-10', 89.0, 91.0, 88.0, 90.10, 1000.0]]));
assert_equals([], $errors, 'Eine Bar nach dem Report-Datum zaehlt nicht als Referenz');

$errors = validate_report(good_doc(), bars_for([
    ['2026-08-05', 40.0, 40.9, 39.7, 42.60, 1000.0],
    ['2026-08-12', 90.0, 91.0, 89.0, 90.00, 1000.0],
]));
assert_equals([], $errors,
    'Referenz ist die letzte Bar bis zum Report-Datum, nicht die letzte insgesamt');

$errors = validate_report(good_doc(), ['XYZ' => []]);
assert_equals([], $errors, 'Leere Bar-Liste ueberspringt die Pruefung');

exit(test_summary());
