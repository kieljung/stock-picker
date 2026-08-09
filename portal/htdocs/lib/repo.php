<?php
// Schreibende Haelfte des Repositories. Jeder Schreibvorgang ist idempotent:
// derselbe Report darf beliebig oft gepusht werden, ohne zu duplizieren und
// ohne die bereits ermittelten Ausgaenge zu verlieren.

const PICK_COLUMNS = [
    'ticker', 'rank', 'name', 'sector', 'setup',
    'score_technical', 'score_catalyst', 'score_risk', 'composite',
    'score_rationale', 'entry', 'stop', 'target1', 'target2',
    'risk_reward', 'position_size', 'catalyst',
    'thesis', 'technicals', 'catalyst_detail', 'plan',
    'bear_case', 'invalidation', 'metrics', 'sources',
];

const JSON_COLUMNS = ['score_rationale', 'plan', 'metrics', 'sources'];

function save_report(PDO $pdo, array $doc): array {
    $date = $doc['date'];
    $picks = $doc['picks'] ?? [];
    $pdo->beginTransaction();
    try {
        $stmt = $pdo->prepare('SELECT 1 FROM reports WHERE date = ?');
        $stmt->execute([$date]);
        $replaced = (bool)$stmt->fetchColumn();

        // Status vor dem Loeschen retten: eine Reportkorrektur darf die
        // bereits ermittelten Ausgaenge nicht mitnehmen.
        $keep = [];
        $stmt = $pdo->prepare(
            'SELECT ticker, status, r_multiple, triggered_on, closed_on, evaluated_at
             FROM picks WHERE report_date = ?');
        $stmt->execute([$date]);
        foreach ($stmt as $row) {
            $keep[$row['ticker']] = $row;
        }

        $pdo->prepare('DELETE FROM picks WHERE report_date = ?')->execute([$date]);
        $pdo->prepare('DELETE FROM reports WHERE date = ?')->execute([$date]);

        $pdo->prepare(
            'INSERT INTO reports (date, generated_at, market_context, note,
                                  rejected, checklist, disclaimer, pushed_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
        )->execute([
            $date, $doc['generated_at'] ?? null, $doc['market_context'] ?? '',
            $doc['note'] ?? '',
            json_encode($doc['rejected'] ?? [], JSON_UNESCAPED_UNICODE),
            json_encode($doc['checklist'] ?? [], JSON_UNESCAPED_UNICODE),
            $doc['disclaimer'] ?? '',
            gmdate('c'),
        ]);

        $columns = array_merge(['report_date'], PICK_COLUMNS,
            ['status', 'r_multiple', 'triggered_on', 'closed_on', 'evaluated_at']);
        $sql = 'INSERT INTO picks (' . implode(', ', $columns) . ') VALUES ('
             . implode(', ', array_fill(0, count($columns), '?')) . ')';
        $insert = $pdo->prepare($sql);

        foreach ($picks as $pick) {
            $values = [$date];
            foreach (PICK_COLUMNS as $column) {
                $value = $pick[$column] ?? null;
                $values[] = in_array($column, JSON_COLUMNS, true)
                    ? json_encode($value ?? [], JSON_UNESCAPED_UNICODE)
                    : $value;
            }
            $previous = $keep[$pick['ticker']] ?? null;
            $values[] = $previous['status'] ?? 'OPEN';
            $values[] = $previous['r_multiple'] ?? null;
            $values[] = $previous['triggered_on'] ?? null;
            $values[] = $previous['closed_on'] ?? null;
            $values[] = $previous['evaluated_at'] ?? null;
            $insert->execute($values);
        }

        $bars = save_bars($pdo, $doc['bars'] ?? [], false);
        $fx   = save_fx($pdo, $doc['fx'] ?? [], false);
        $pdo->commit();
    } catch (Throwable $exc) {
        $pdo->rollBack();
        throw $exc;
    }

    return ['date' => $date, 'picks' => count($picks),
            'bars_written' => $bars, 'fx_written' => $fx, 'replaced' => $replaced];
}

function save_bars(PDO $pdo, array $bars, bool $own_transaction = true): int {
    if (!$bars) {
        return 0;
    }
    if ($own_transaction) {
        $pdo->beginTransaction();
    }
    $stmt = $pdo->prepare(
        'INSERT INTO bars (ticker, date, open, high, low, close, volume)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(ticker, date) DO UPDATE SET
             open = excluded.open, high = excluded.high, low = excluded.low,
             close = excluded.close, volume = excluded.volume');
    $written = 0;
    foreach ($bars as $ticker => $rows) {
        foreach ($rows as $row) {
            $stmt->execute([$ticker, $row[0], $row[1], $row[2], $row[3], $row[4], $row[5]]);
            $written++;
        }
    }
    if ($own_transaction) {
        $pdo->commit();
    }
    return $written;
}

/**
 * EUR/USD-Tageskurse, idempotent wie die Bars.
 *
 * Erwartet [[datum, kurs], ...] mit dem Kurs als USD je EUR -- die Notierung
 * aus stockpicker/fx.py. Ein spaeterer Push darf einen Tag korrigieren:
 * yfinance liefert den Schlusskurs des laufenden Tages vorlaeufig.
 */
function save_fx(PDO $pdo, array $rows, bool $own_transaction = true): int {
    if (!$rows) {
        return 0;
    }
    if ($own_transaction) {
        $pdo->beginTransaction();
    }
    $stmt = $pdo->prepare(
        'INSERT INTO fx (date, rate) VALUES (?, ?)
         ON CONFLICT(date) DO UPDATE SET rate = excluded.rate');
    $written = 0;
    foreach ($rows as $row) {
        if (!is_array($row) || count($row) < 2 || !is_numeric($row[1])) {
            continue;
        }
        $stmt->execute([(string)$row[0], (float)$row[1]]);
        $written++;
    }
    if ($own_transaction) {
        $pdo->commit();
    }
    return $written;
}

function save_outcomes(PDO $pdo, array $outcomes): int {
    $stmt = $pdo->prepare(
        'UPDATE picks SET status = ?, r_multiple = ?, evaluated_at = ?
         WHERE report_date = ? AND ticker = ?');
    $updated = 0;
    $pdo->beginTransaction();
    try {
        foreach ($outcomes as $outcome) {
            $stmt->execute([$outcome['status'], $outcome['r_multiple'] ?? null,
                            gmdate('c'), $outcome['date'], $outcome['ticker']]);
            $updated += $stmt->rowCount();
        }
        $pdo->commit();
    } catch (Throwable $exc) {
        $pdo->rollBack();
        throw $exc;
    }
    return $updated;
}

function log_push(PDO $pdo, string $endpoint, int $bytes, int $status, string $detail = ''): void {
    $pdo->prepare('INSERT INTO pushes (endpoint, at, bytes, status, detail)
                   VALUES (?, ?, ?, ?, ?)')
        ->execute([$endpoint, gmdate('c'), $bytes, $status, mb_substr($detail, 0, 500)]);
}

// ---------------------------------------------------------------------------
// Lesende Haelfte. Die Aggregation ist das PHP-Gegenstueck zu
// stockpicker/outcomes.py:_aggregate und :summarize -- dieselben Status,
// dieselbe Rundung, dieselben Buckets. Weicht hier etwas ab, widersprechen
// sich Portal und review.py bei denselben Daten.

const CLOSED_STATUS  = "('TARGET1','STOP')";     // outcomes._aggregate
const CATALYST_BUCKETS = [                        // outcomes.CATALYST_BUCKETS
    ['stark (7-10)', 7, 10],
    ['mittel (4-6)', 4, 6],
    ['schwach (0-3)', 0, 3],
];

// Spiegel von TradeConfig.account_risk_pct in stockpicker/config.py.
const ACCOUNT_RISK_PCT = 1.0;

/**
 * Der EUR/USD-Kurs, mit dem eine Seite rechnet: der juengste am oder vor
 * $on. Ohne $on der zuletzt bekannte.
 *
 * Bewusst der Kurs des Reporttages und nicht der von heute: ein Report ist
 * das Dokument seines Tages. Wuerde er mit dem Tageskurs gerechnet, aenderten
 * sich die EUR-Niveaus eines abgeschlossenen Trades noch Monate spaeter.
 *
 * Gibt ['date' => …, 'rate' => …] zurueck oder null, wenn das Portal noch
 * keinen Kurs kennt -- dann zeigen die Views nur USD.
 */
function fx_rate(PDO $pdo, ?string $on = null): ?array {
    if ($on === null) {
        $row = $pdo->query('SELECT date, rate FROM fx ORDER BY date DESC LIMIT 1')->fetch();
    } else {
        $stmt = $pdo->prepare(
            'SELECT date, rate FROM fx WHERE date <= ? ORDER BY date DESC LIMIT 1');
        $stmt->execute([$on]);
        $row = $stmt->fetch();
    }
    if (!$row || !is_numeric($row['rate']) || (float)$row['rate'] <= 0) {
        return null;
    }
    return ['date' => (string)$row['date'], 'rate' => (float)$row['rate']];
}

/**
 * USD-Betrag in EUR. $rate ist USD je EUR.
 *
 * Ohne Kurs kommt null zurueck, nie der USD-Betrag: eine unumgerechnete Zahl
 * mit einem Eurozeichen daneben waere die teuerste Art, hier zu scheitern.
 */
function to_eur(?float $usd, ?float $rate): ?float {
    if ($usd === null || $rate === null || $rate <= 0) {
        return null;
    }
    return $usd / $rate;
}

// Bars je Pick fuer die Miniatur-Charts der Reportseite.
const MINI_CHART_BARS = 60;

/**
 * Rechnet R:R und Stueckzahl aus Entry, Stop und Target 1 neu.
 *
 * Beide Felder liefert das Modell im Report mit, aber es sind reine
 * Rechengroessen aus drei Zahlen, die daneben stehen. Dem Modell bei
 * Arithmetik zu glauben, waehrend die Eingaben vorliegen, waere eine
 * vermeidbare Fehlerquelle -- also wird gerechnet und der gespeicherte Wert
 * ueberschrieben. Die Kontogroesse kommt von aussen herein; repo.php kennt
 * config.php nicht.
 *
 * Das Konto steht in EUR, das Risiko je Stueck in USD. Ohne Devisenkurs
 * bleibt die Stueckzahl deshalb leer: eine Position, die den Wechselkurs
 * stillschweigend mit 1,00 ansetzt, ist bei EUR/USD 1,16 um 16 % zu gross.
 */
function with_derived_levels(array $pick, float $account, ?float $rate = null): array {
    $entry = $pick['entry'] ?? null;
    $stop  = $pick['stop'] ?? null;
    $t1    = $pick['target1'] ?? null;

    $risk = (is_numeric($entry) && is_numeric($stop)) ? (float)$entry - (float)$stop : 0.0;
    if ($risk <= 0 || !is_numeric($t1)) {
        // Ohne belastbares Risiko gibt es nichts zu rechnen. Lieber nichts
        // anzeigen als eine Zahl, die aus einer Division durch fast null kommt.
        $pick['risk_reward']   = null;
        $pick['position_size'] = null;
        return $pick;
    }
    $pick['risk_reward'] = round(((float)$t1 - (float)$entry) / $risk, 2);
    $risk_eur = to_eur($risk, $rate);
    $pick['position_size'] = $risk_eur === null
        ? null
        : (int)floor($account * ACCOUNT_RISK_PCT / 100.0 / $risk_eur);
    return $pick;
}

function decode_json_column(?string $raw): array {
    $decoded = json_decode((string)$raw, true);
    return is_array($decoded) ? $decoded : [];
}

function decode_pick(array $row, float $account, ?float $rate = null): array {
    foreach (JSON_COLUMNS as $column) {
        $row[$column] = decode_json_column($row[$column] ?? null);
    }
    return with_derived_levels($row, $account, $rate);
}

function list_reports(PDO $pdo, float $account, int $limit = 50): array {
    $limit = max(1, min(500, $limit));   // int-getypt, aber nicht unbegrenzt
    $reports = $pdo->query(
        "SELECT * FROM reports ORDER BY date DESC LIMIT $limit")->fetchAll();

    // Entry, Stop und Target 1 stehen hier nur mit, damit R:R und Stueckzahl
    // auch in der Uebersicht gerechnet statt uebernommen werden.
    $stmt = $pdo->prepare(
        'SELECT ticker, name, setup, composite, status, r_multiple,
                entry, stop, target1
         FROM picks WHERE report_date = ? ORDER BY rank');
    foreach ($reports as &$report) {
        $report['rejected']  = decode_json_column($report['rejected'] ?? null);
        $report['checklist'] = decode_json_column($report['checklist'] ?? null);
        $stmt->execute([$report['date']]);
        $rate = fx_rate($pdo, (string)$report['date']);
        $report['fx'] = $rate;
        $report['picks'] = array_map(
            fn(array $pick): array => with_derived_levels($pick, $account, $rate['rate'] ?? null),
            $stmt->fetchAll());
    }
    unset($report);   // sonst zeigt $report weiter in das letzte Element
    return $reports;
}

function get_report(PDO $pdo, string $date, float $account): ?array {
    $stmt = $pdo->prepare('SELECT * FROM reports WHERE date = ?');
    $stmt->execute([$date]);
    $report = $stmt->fetch();
    if (!$report) {
        return null;
    }
    $report['rejected']  = decode_json_column($report['rejected'] ?? null);
    $report['checklist'] = decode_json_column($report['checklist'] ?? null);

    $report['fx'] = fx_rate($pdo, $date);
    $rate = $report['fx']['rate'] ?? null;

    $stmt = $pdo->prepare('SELECT * FROM picks WHERE report_date = ? ORDER BY rank');
    $stmt->execute([$date]);
    $report['picks'] = array_map(
        fn(array $pick): array => decode_pick($pick, $account, $rate), $stmt->fetchAll());

    // Die Reportseite zeichnet je Pick einen Miniatur-Chart und braucht dafuer
    // Bars. Nur der juengste Ausschnitt: die Miniatur ist ein paar Zentimeter
    // breit, die volle Historie steht auf der Detailseite.
    $bars = $pdo->prepare(
        'SELECT date, open, high, low, close, volume FROM bars
         WHERE ticker = ? ORDER BY date DESC LIMIT ' . MINI_CHART_BARS);
    foreach ($report['picks'] as &$pick) {
        $bars->execute([$pick['ticker']]);
        $pick['bars'] = array_reverse($bars->fetchAll());
    }
    unset($pick);   // sonst zeigt $pick weiter in das letzte Element
    return $report;
}

function get_pick(PDO $pdo, string $date, string $ticker, float $account): ?array {
    $stmt = $pdo->prepare('SELECT * FROM picks WHERE report_date = ? AND ticker = ?');
    $stmt->execute([$date, $ticker]);
    $pick = $stmt->fetch();
    if (!$pick) {
        return null;
    }
    $pick['fx'] = fx_rate($pdo, $date);
    $pick = decode_pick($pick, $account, $pick['fx']['rate'] ?? null);
    $stmt = $pdo->prepare(
        'SELECT date, open, high, low, close, volume FROM bars
         WHERE ticker = ? ORDER BY date');
    $stmt->execute([$ticker]);
    $pick['bars'] = $stmt->fetchAll();
    return $pick;
}

function performance_overall(PDO $pdo, ?string $from = null, ?string $to = null): array {
    $where = date_filter($from, $to);
    $row = $pdo->query(
        "SELECT COUNT(*) AS trades,
                SUM(CASE WHEN status='TARGET1' THEN 1 ELSE 0 END) AS wins,
                COALESCE(SUM(COALESCE(r_multiple, 0)), 0) AS total_r
         FROM picks WHERE status IN " . CLOSED_STATUS . " $where")->fetch();

    $trades = (int)$row['trades'];
    return [
        'trades'        => $trades,
        'win_rate'      => $trades ? round_half_even(100.0 * (int)$row['wins'] / $trades, 1) : 0.0,
        'expectancy_r'  => $trades ? round_half_even((float)$row['total_r'] / $trades, 2) : 0.0,
        'not_triggered' => (int)$pdo->query(
            "SELECT COUNT(*) FROM picks WHERE status='NOT_TRIGGERED' $where")->fetchColumn(),
        'open'          => (int)$pdo->query(
            "SELECT COUNT(*) FROM picks WHERE status='OPEN' $where")->fetchColumn(),
    ];
}

/**
 * Gruppierte Aggregation, gebaut wie outcomes.summarize: gruppiert wird ueber
 * alle Zeilen, gerechnet nur ueber die geschlossenen. Eine Gruppe, in der noch
 * kein Trade geschlossen ist, erscheint deshalb mit Nullen statt zu fehlen --
 * dass ein Setup noch nichts abgeschlossen hat, ist selbst eine Auskunft.
 */
function performance_by(PDO $pdo, string $dimension,
                        ?string $from = null, ?string $to = null): array {
    if ($dimension === 'setup') {
        // outcomes.summarize: result.get("setup", "UNKNOWN")
        $key = "COALESCE(setup, 'UNKNOWN')";
    } elseif ($dimension === 'catalyst_bucket') {
        $key = catalyst_bucket_sql();
    } else {
        throw new InvalidArgumentException("Unbekannte Dimension '$dimension'");
    }

    $where = date_filter($from, $to);
    $rows = $pdo->query(
        "SELECT $key AS bucket,
                SUM(CASE WHEN status IN " . CLOSED_STATUS . " THEN 1 ELSE 0 END) AS trades,
                SUM(CASE WHEN status='TARGET1' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN status IN " . CLOSED_STATUS . "
                         THEN COALESCE(r_multiple, 0) ELSE 0 END) AS total_r
         FROM picks WHERE 1=1 $where
         GROUP BY bucket HAVING bucket IS NOT NULL ORDER BY bucket")->fetchAll();

    $result = [];
    foreach ($rows as $row) {
        $trades = (int)$row['trades'];
        $result[$row['bucket']] = [
            'trades'       => $trades,
            'win_rate'     => $trades
                ? round_half_even(100.0 * (int)$row['wins'] / $trades, 1) : 0.0,
            'expectancy_r' => $trades
                ? round_half_even((float)$row['total_r'] / $trades, 2) : 0.0,
        ];
    }
    return $result;
}

/**
 * CASE-Ausdruck aus CATALYST_BUCKETS. Ein Score ausserhalb aller Baender --
 * auch NULL -- ergibt NULL und faellt damit aus der Gruppierung, genau wie
 * outcomes._bucket_for None liefert und summarize die Zeile ueberspringt.
 */
function catalyst_bucket_sql(): string {
    $sql = 'CASE';
    foreach (CATALYST_BUCKETS as [$name, $low, $high]) {
        $sql .= " WHEN score_catalyst BETWEEN $low AND $high THEN " . quote_literal($name);
    }
    return $sql . ' ELSE NULL END';
}

function quote_literal(string $value): string {
    return "'" . str_replace("'", "''", $value) . "'";
}

function cumulative_r(PDO $pdo, ?string $from = null, ?string $to = null): array {
    $where = date_filter($from, $to);
    $rows = $pdo->query(
        "SELECT report_date, ticker, COALESCE(r_multiple, 0) AS r_multiple FROM picks
         WHERE status IN " . CLOSED_STATUS . " $where
         ORDER BY report_date, ticker")->fetchAll();

    $sum = 0.0;
    $curve = [];
    foreach ($rows as $row) {
        $sum += (float)$row['r_multiple'];
        $curve[] = ['date' => $row['report_date'], 'ticker' => $row['ticker'],
                    'r' => (float)$row['r_multiple'], 'cumulative' => round($sum, 2)];
    }
    return $curve;
}

/**
 * Datumsklausel fuer die Aggregation. Die Werte stammen aus der Query-String,
 * also passiert nur, was exakt ein ISO-Datum ist; alles andere faellt heraus
 * und der Filter entfaellt. Anker sind \A und \z statt ^ und $: ein
 * abschliessendes Zeilenende wuerde $ noch durchlassen.
 */
function date_filter(?string $from, ?string $to): string {
    $iso = '/\A\d{4}-\d{2}-\d{2}\z/';
    $clauses = [];
    if ($from && preg_match($iso, $from)) {
        $clauses[] = "report_date >= '$from'";
    }
    if ($to && preg_match($iso, $to)) {
        $clauses[] = "report_date <= '$to'";
    }
    return $clauses ? ' AND ' . implode(' AND ', $clauses) : '';
}

/**
 * Rundet wie Pythons round(): auf dem exakten Wert der Gleitkommazahl, und
 * bei genau der Haelfte zur geraden Ziffer.
 *
 * PHPs round() rundet die Haelfte von der Null weg und kennt einen Modus erst
 * ab 8.4. Nachrechnen mit Multiplikation hilft nicht: 2,675 mal 100 ergibt in
 * Gleitkomma exakt 267,5, obwohl die Zahl selbst knapp darunter liegt --
 * Python rundet deshalb auf 2,67, jede Multiplikationsloesung auf 2,68.
 * Also wird auf der Dezimaldarstellung gerundet, die sprintf ausgibt: sie
 * zeigt weit hinter der gesuchten Stelle noch den echten Wert.
 */
function round_half_even(float $value, int $precision): float {
    if (!is_finite($value)) {
        return $value;
    }
    $negative = $value < 0;
    [$int, $frac] = explode('.',
        sprintf('%.' . ($precision + 20) . 'F', $negative ? -$value : $value));

    $digits = $int . substr($frac, 0, $precision);   // Ziffern ohne Komma
    $rest   = substr($frac, $precision);
    $half   = '5' . str_repeat('0', strlen($rest) - 1);

    $comparison = strcmp($rest, $half);              // gleich lange Ziffernfolgen
    if ($comparison > 0
        || ($comparison === 0 && (int)$digits[strlen($digits) - 1] % 2 === 1)) {
        $digits = increment_digit_string($digits);
    }

    $result = $precision > 0
        ? substr($digits, 0, -$precision) . '.' . substr($digits, -$precision)
        : $digits;
    return (float)($negative ? "-$result" : $result);
}

function increment_digit_string(string $digits): string {
    for ($i = strlen($digits) - 1; $i >= 0; $i--) {
        if ($digits[$i] !== '9') {
            $digits[$i] = (string)((int)$digits[$i] + 1);
            return $digits;
        }
        $digits[$i] = '0';
    }
    return '1' . $digits;
}
