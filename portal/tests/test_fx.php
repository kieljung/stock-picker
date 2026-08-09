<?php
// Die EUR-Anzeigeschicht. Gerechnet wird im Portal weiter in USD -- geprueft
// wird hier, dass die Umrechnung an genau den Stellen greift, an denen eine
// Order entsteht, und dass sie ohne Kurs lieber schweigt als raet.

require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';
require __DIR__ . '/../htdocs/lib/validate.php';
require __DIR__ . '/../htdocs/lib/repo.php';
require __DIR__ . '/fixtures.php';

$pdo = db_connect(':memory:');
migrate($pdo);

// --- Speicherung -----------------------------------------------------------

assert_equals(2, save_fx($pdo, [['2026-08-07', 1.0900], ['2026-08-09', 1.1000]]),
    'Zwei Kurse geschrieben');
assert_equals(2, save_fx($pdo, [['2026-08-07', 1.0912], ['2026-08-09', 1.1000]]),
    'Erneuter Push schreibt dieselben Tage');
assert_equals(2, (int)$pdo->query('SELECT COUNT(*) FROM fx')->fetchColumn(),
    'Weiterhin genau zwei Tage');
assert_equals(1.0912,
    (float)$pdo->query("SELECT rate FROM fx WHERE date='2026-08-07'")->fetchColumn(),
    'Der korrigierte Kurs gewinnt');
assert_equals(0, save_fx($pdo, []), 'Leere Liste schreibt nichts');
assert_equals(0, save_fx($pdo, [['2026-08-10', 'keine Zahl']]),
    'Kaputte Zeile wird uebersprungen statt geschrieben');

// --- Kurswahl --------------------------------------------------------------

assert_equals(1.1000, fx_rate($pdo, '2026-08-09')['rate'], 'Kurs des Tages');
assert_equals('2026-08-07', fx_rate($pdo, '2026-08-08')['date'],
    'Am Devisenfeiertag zaehlt der Vortag');
assert_equals(null, fx_rate($pdo, '2026-08-01'),
    'Vor dem ersten bekannten Kurs wird nicht geraten');
assert_equals('2026-08-09', fx_rate($pdo)['date'], 'Ohne Datum der juengste Kurs');

// --- Umrechnung ------------------------------------------------------------

assert_equals(20.0, round(to_eur(21.80, 1.09), 2), '21,80 USD sind 20,00 EUR');
assert_equals(null, to_eur(21.80, null), 'Ohne Kurs kein EUR-Betrag');
assert_equals(null, to_eur(null, 1.09), 'Ohne USD-Betrag kein EUR-Betrag');
assert_equals(null, to_eur(21.80, 0.0), 'Kurs null rechnet nicht gegen unendlich');

// --- Positionsgroesse ------------------------------------------------------
//
// Konto 10.000 EUR, 1 % Risiko = 100 EUR. Risiko je Stueck 2,70 USD, bei
// EUR/USD 1,09 also 2,4771 EUR -> 40 Stueck. Ohne Umrechnung waeren es 37 --
// die Position waere um den Wechselkurs zu klein.
$pick = with_derived_levels(fixture_pick(), 10000.0, 1.09);
assert_equals(2.0, $pick['risk_reward'], 'R:R bleibt vom Wechselkurs unberuehrt');
assert_equals(40, $pick['position_size'], 'Stueckzahl rechnet gegen ein EUR-Konto');

$without = with_derived_levels(fixture_pick(), 10000.0, null);
assert_equals(null, $without['position_size'],
    'Ohne Devisenkurs bleibt die Stueckzahl leer statt falsch');
assert_equals(2.0, $without['risk_reward'], 'R:R steht auch ohne Devisenkurs');

// --- Validierung -----------------------------------------------------------

assert_equals([], validate_fx([]), 'Leere Kursliste ist zulaessig');
assert_equals([], validate_fx([['2026-08-07', 1.09]]), 'Gueltiger Kurs passiert');
assert_that(count(validate_fx([['07.08.2026', 1.09]])) === 1,
    'Datum ohne ISO-Format wird abgelehnt');
assert_that(count(validate_fx([['2026-08-07', 'eins']])) === 1,
    'Kurs ohne Zahl wird abgelehnt');
assert_that(count(validate_fx([['2026-08-07', 109.0]])) === 1,
    'Kurs ausserhalb des Plausibilitaetsbands wird abgelehnt');
assert_that(count(validate_fx([['2026-08-07']])) === 1,
    'Zeile ohne Kurs wird abgelehnt statt fatal');

// --- Weg durch den Report --------------------------------------------------

$doc = fixture_document();
$doc['fx'] = [['2026-08-08', 1.0850], ['2026-08-09', 1.0900]];
assert_equals([], validate_fx($doc['fx']), 'Kurse im Report passieren die Pruefung');

$result = save_report($pdo, $doc);
assert_equals(2, $result['fx_written'], 'Der Report bringt seine Kurse mit');

$report = get_report($pdo, '2026-08-09', 10000.0);
assert_equals(1.0900, $report['fx']['rate'], 'Der Report kennt seinen Kurs');
assert_that($report['picks'][0]['position_size'] !== null,
    'Die Uebersicht rechnet die Stueckzahl mit dem Kurs des Reporttages');

$detail = get_pick($pdo, '2026-08-09', 'XYZ', 10000.0);
assert_equals('2026-08-09', $detail['fx']['date'], 'Die Detailseite kennt den Kurs');
assert_equals(round(42.80 / 1.09, 4), round(to_eur((float)$detail['entry'], $detail['fx']['rate']), 4),
    'Der Entry der Detailseite laesst sich in EUR umrechnen');

// Ein Report ohne Kurse bleibt zulaessig -- er zeigt dann nur USD.
$older = fixture_document();
$older['date'] = '2026-07-01';
unset($older['fx']);
save_report($pdo, $older);
assert_equals(null, get_report($pdo, '2026-07-01', 10000.0)['fx'],
    'Ein Tag vor dem ersten Kurs bleibt ohne EUR-Anzeige');
assert_equals(null, get_report($pdo, '2026-07-01', 10000.0)['picks'][0]['position_size'],
    'Und traegt dort auch keine Stueckzahl');

exit(test_summary());
