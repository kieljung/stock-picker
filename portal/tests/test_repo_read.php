<?php
require __DIR__ . '/bootstrap.php';
require __DIR__ . '/../htdocs/lib/db.php';
require __DIR__ . '/../htdocs/lib/migrate.php';
require __DIR__ . '/../htdocs/lib/repo.php';
require __DIR__ . '/fixtures.php';

// Die Kontogroesse kommt aus derselben Datei wie im Betrieb; repo.php selbst
// liest sie nie, sondern bekommt sie hereingereicht.
$config  = require __DIR__ . '/../htdocs/lib/config.php';
$account = (float)$config['account'];
assert_equals(10000.0, $account, 'Kontogroesse aus config.php');

$pdo = db_connect(':memory:');
migrate($pdo);
save_report($pdo, fixture_document());

$reports = list_reports($pdo, $account);
assert_equals(1, count($reports), 'Ein Report gelistet');
assert_equals(2, count($reports[0]['picks']), 'Mit seinen zwei Picks');

$report = get_report($pdo, '2026-08-09', $account);
assert_equals('2026-08-09', $report['date'], 'Report nach Datum gefunden');
assert_that(is_array($report['picks'][0]['metrics']), 'metrics ist dekodiert');
assert_that(is_array($report['picks'][0]['sources']), 'sources ist dekodiert');
assert_equals(5, count($report['picks'][0]['bars']),
    'Report-Picks bringen ihre Bars fuer den Miniatur-Chart mit');
assert_equals('2026-08-03', $report['picks'][0]['bars'][0]['date'],
    'Bars stehen aufsteigend, aeltester zuerst');

assert_equals(null, get_report($pdo, '2020-01-01', $account), 'Unbekanntes Datum liefert null');

$pick = get_pick($pdo, '2026-08-09', 'XYZ', $account);
assert_equals('XYZ', $pick['ticker'], 'Pick gefunden');
assert_that(is_array($pick['bars']), 'Pick bringt seine Bars mit');
assert_that(is_array($pick['plan']), 'plan ist dekodiert');
assert_equals(null, get_pick($pdo, '2026-08-09', 'NOPE', $account), 'Unbekannter Ticker liefert null');

// --- Abgeleitete Werte: nie dem Modell glauben, wenn man rechnen kann -----

// XYZ: (48.20 - 42.80) / (42.80 - 40.10) = 2.0, floor(100 / 2.70) = 37
assert_equals(2.0, $pick['risk_reward'], 'R:R aus Entry, Stop und Target 1 gerechnet');
assert_equals(37, $pick['position_size'], 'Positionsgroesse aus einem Prozent Kontorisiko');

// ABC: (34.50 - 30.00) / (30.00 - 28.00) = 2.25, floor(100 / 2.00) = 50
$abc = get_pick($pdo, '2026-08-09', 'ABC', $account);
assert_equals(2.25, $abc['risk_reward'], 'R:R des zweiten Picks');
assert_equals(50, $abc['position_size'], 'Positionsgroesse des zweiten Picks');

// Gespeicherte Werte sind Modell-Arithmetik und werden beim Lesen ueberschrieben.
$pdo->exec("UPDATE picks SET risk_reward = 9.99, position_size = 1
            WHERE report_date = '2026-08-09' AND ticker = 'XYZ'");
assert_equals(2.0, get_pick($pdo, '2026-08-09', 'XYZ', $account)['risk_reward'],
    'Falsches gespeichertes R:R wird in get_pick korrigiert');
assert_equals(37, get_pick($pdo, '2026-08-09', 'XYZ', $account)['position_size'],
    'Falsche gespeicherte Positionsgroesse wird in get_pick korrigiert');

$corrected = get_report($pdo, '2026-08-09', $account)['picks'][0];
assert_equals(2.0, $corrected['risk_reward'], 'get_report korrigiert ebenso');
assert_equals(37, $corrected['position_size'], 'get_report korrigiert auch die Stueckzahl');

$listed = list_reports($pdo, $account)[0]['picks'][0];
assert_equals(2.0, $listed['risk_reward'], 'list_reports korrigiert ebenso');
assert_equals(37, $listed['position_size'], 'list_reports korrigiert auch die Stueckzahl');

// Eine halbierte Kontogroesse halbiert die Stueckzahl, nicht das Verhaeltnis.
$half = get_pick($pdo, '2026-08-09', 'XYZ', 5000.0);
assert_equals(18, $half['position_size'], 'Kontogroesse geht in die Stueckzahl ein');
assert_equals(2.0, $half['risk_reward'], 'R:R haengt nicht an der Kontogroesse');

// --- Aggregation, Zahl fuer Zahl wie stockpicker/outcomes.py --------------

// Drei geschlossene Trades: zwei Gewinner mit 2R, ein Verlierer mit -1R.
// Trefferquote 66,7 %, Erwartungswert (2 + 2 - 1) / 3 = 1,0R.
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.0 WHERE ticker='XYZ'");
$pdo->exec("UPDATE picks SET status='STOP', r_multiple=-1.0 WHERE ticker='ABC'");
save_report($pdo, fixture_document(['date' => '2026-08-10']));
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.0
            WHERE report_date='2026-08-10' AND ticker='XYZ'");
$pdo->exec("UPDATE picks SET status='NOT_TRIGGERED', r_multiple=0
            WHERE report_date='2026-08-10' AND ticker='ABC'");

$overall = performance_overall($pdo);
assert_equals(3, $overall['trades'], 'Drei geschlossene Trades');
assert_equals(66.7, $overall['win_rate'], 'Trefferquote 66,7 Prozent');
assert_equals(1.0, $overall['expectancy_r'], 'Erwartungswert 1,0R');
assert_equals(1, $overall['not_triggered'], 'Ein nicht ausgeloester Pick');
assert_equals(0, $overall['open'], 'Kein offener Pick mehr');

$by_setup = performance_by($pdo, 'setup');
assert_that(isset($by_setup['VCP_BREAKOUT']), 'Nach Setup gruppiert');
assert_equals(2, $by_setup['VCP_BREAKOUT']['trades'], 'VCP_BREAKOUT mit zwei Trades');
assert_equals(100.0, $by_setup['VCP_BREAKOUT']['win_rate'], 'VCP_BREAKOUT ohne Verlierer');
assert_equals(2.0, $by_setup['VCP_BREAKOUT']['expectancy_r'], 'VCP_BREAKOUT mit 2,0R');
assert_equals(1, $by_setup['FLAT_BASE']['trades'], 'FLAT_BASE mit einem Trade');
assert_equals(0.0, $by_setup['FLAT_BASE']['win_rate'], 'FLAT_BASE ohne Gewinner');
assert_equals(-1.0, $by_setup['FLAT_BASE']['expectancy_r'], 'FLAT_BASE mit -1,0R');

$by_catalyst = performance_by($pdo, 'catalyst_bucket');
assert_that(isset($by_catalyst['stark (7-10)']), 'Katalysator-Buckets wie in outcomes.py');
assert_equals(3, $by_catalyst['stark (7-10)']['trades'], 'Alle drei im starken Bucket');
assert_equals(1, count($by_catalyst), 'Kein leerer Bucket erfunden');

$curve = cumulative_r($pdo);
assert_equals(3, count($curve), 'Kurve hat einen Punkt je geschlossenem Trade');
assert_equals(3.0, end($curve)['cumulative'], 'Kumulierte R am Ende 3,0');
assert_equals(-1.0, $curve[0]['cumulative'], 'Kurve beginnt beim ersten Verlierer');

// outcomes.summarize gruppiert ueber alle Zeilen und aggregiert nur die
// geschlossenen: eine Gruppe ohne geschlossenen Trade erscheint mit Nullen.
save_report($pdo, fixture_document([
    'date'  => '2026-08-11',
    'picks' => [fixture_pick(['ticker' => 'MRV', 'setup' => 'MEAN_REVERSION'])],
    'bars'  => [],
]));
$pdo->exec("UPDATE picks SET status='NOT_TRIGGERED', r_multiple=0
            WHERE report_date='2026-08-11'");
$by_setup = performance_by($pdo, 'setup');
assert_that(isset($by_setup['MEAN_REVERSION']), 'Gruppe ohne geschlossenen Trade erscheint');
assert_equals(0, $by_setup['MEAN_REVERSION']['trades'], 'Sie zaehlt null Trades');
assert_equals(0.0, $by_setup['MEAN_REVERSION']['win_rate'], 'Und null Prozent');
assert_equals(0.0, $by_setup['MEAN_REVERSION']['expectancy_r'], 'Und null R');

// outcomes._bucket_for gibt None zurueck, wenn der Score fehlt oder ausserhalb
// von 0-10 liegt. Solche Zeilen tauchen in keinem Bucket auf.
save_report($pdo, fixture_document([
    'date'  => '2026-08-12',
    'picks' => [fixture_pick(['ticker' => 'NUL'])],
    'bars'  => [],
]));
$pdo->exec("UPDATE picks SET status='TARGET1', r_multiple=2.0, score_catalyst=NULL
            WHERE report_date='2026-08-12'");
$by_catalyst = performance_by($pdo, 'catalyst_bucket');
assert_equals(1, count($by_catalyst), 'Pick ohne Katalysator-Score bekommt keinen Bucket');
assert_equals(3, $by_catalyst['stark (7-10)']['trades'], 'Und faellt in keinen fremden Bucket');
assert_equals(4, performance_overall($pdo)['trades'], 'In der Gesamtzahl zaehlt er trotzdem');

// --- Datumsfilter ---------------------------------------------------------

assert_equals(1, performance_overall($pdo, '2026-08-10', '2026-08-10')['trades'],
    'Datumsfilter grenzt auf einen Trade ein');
assert_equals(3, performance_overall($pdo, null, '2026-08-10')['trades'],
    'Nur obere Grenze wirkt ebenso');
assert_equals(1, count(cumulative_r($pdo, '2026-08-10', '2026-08-10')),
    'Kurve folgt demselben Filter');

// Die Werte kommen aus der Query-String. Die Regex-Wache laesst nur ISO-Daten
// durch; alles andere faellt heraus, statt in SQL zu landen.
$injection = "2026-01-01'; DROP TABLE picks; --";
assert_equals('', date_filter($injection, $injection), 'Injektionsversuch erzeugt keine Klausel');
assert_equals('', date_filter("2026-01-01\n", null),
    'Datum mit angehaengter Zeile faellt ebenfalls heraus');
assert_equals(4, performance_overall($pdo, $injection, null)['trades'],
    'Der Versuch bleibt wirkungslos');
$by_setup = performance_by($pdo, 'setup', $injection, $injection);
assert_that(isset($by_setup['VCP_BREAKOUT']), 'Auch in der Gruppierung wirkungslos');
assert_equals(4, count(cumulative_r($pdo, null, $injection)), 'Auch in der Kurve wirkungslos');
assert_equals(1, (int)$pdo->query(
    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='picks'")->fetchColumn(),
    'Tabelle picks existiert noch');

// --- Rundung wie Pythons round() ------------------------------------------

// Genau die Haelfte geht zur geraden Ziffer: 5 von 16 Gewinnern sind 31,25 %,
// Python rundet auf 31,2, PHPs round() auf 31,3.
assert_equals(31.2, round_half_even(31.25, 1), 'Haelfte rundet zur geraden Ziffer');
assert_equals(18.8, round_half_even(18.75, 1), 'Und zwar in beide Richtungen');
// 2,675 liegt als Gleitkommazahl knapp unter der Haelfte, 2,675 * 100 aber
// exakt darauf -- deshalb wird auf der Dezimaldarstellung gerundet.
assert_equals(2.67, round_half_even(2.675, 2), 'Kein Scheintreffer durch Multiplikation');
assert_equals(-1.0, round_half_even(-1.0, 2), 'Negative Werte bleiben negativ');
assert_equals(10.0, round_half_even(9.99, 1), 'Uebertrag ueber alle Stellen');

$caught = false;
try {
    performance_by($pdo, 'ticker');
} catch (InvalidArgumentException $exc) {
    $caught = true;
}
assert_that($caught, 'Unbekannte Dimension wird abgelehnt statt still zu Setup zu werden');

exit(test_summary());
