<?php
// Spiegel von stockpicker/ledger.py und stockpicker/report.py. Die Schwellen
// stehen dort in config.py -- aendert sich eine, aendert sie sich hier mit.
//
// Der Entry-Abgleich aus stockpicker/ledger.py (MAX_ENTRY_DEVIATION_PCT) ist
// hier moeglich, weil der Report-Payload die Bars mitbringt: die stammen aus
// yfinance, nicht aus dem Modell, also prueft sich das Modell nicht selbst.

// Schaerfste Halluzinationspruefung der Pipeline; Gegenstueck zu
// MAX_ENTRY_DEVIATION_PCT in stockpicker/ledger.py.
const MAX_ENTRY_DEVIATION_PCT = 15.0;

const MIN_RISK_REWARD    = 2.0;
const MIN_COMPOSITE      = 60;
const MAX_PICKS          = 5;
const MAX_PER_SECTOR     = 2;

const DOCUMENT_FIELDS = ['date', 'generated_at', 'market_context', 'picks'];

const REQUIRED_PROSE = ['thesis', 'technicals', 'catalyst_detail',
                        'bear_case', 'invalidation'];

const REQUIRED_PICK_FIELDS = [
    'ticker', 'name', 'sector', 'setup', 'rank',
    'score_technical', 'score_catalyst', 'score_risk', 'composite',
    'score_rationale', 'entry', 'stop', 'target1', 'target2',
    'catalyst', 'plan', 'metrics', 'sources',
    'thesis', 'technicals', 'catalyst_detail', 'bear_case', 'invalidation',
];

const RATIONALE_KEYS = ['technical', 'catalyst', 'risk'];
const PLAN_KEYS      = ['entry', 'stop', 'target1', 'target2'];

// Plausibilitaetsband fuer EUR/USD. Der Kurs stand seit Einfuehrung der
// Waehrung zwischen 0,82 und 1,60; die Grenzen sind bewusst weit. Sie fangen
// nicht den falschen Kurs ab, sondern die vertauschte Notierung -- ein
// versehentlich als USD-je-EUR gespeicherter Kehrwert von 0,92 landet bei
// 1,09 und faellt hier nicht auf, ein Faktor 100 dagegen schon.
const FX_MIN_RATE = 0.5;
const FX_MAX_RATE = 2.0;

/**
 * Prueft [[datum, kurs], ...]. Eine leere Liste ist in Ordnung: ein Report
 * ohne Devisenkurse ist zulaessig, er zeigt dann nur USD.
 */
function validate_fx(array $rows): array {
    $errors = [];
    foreach (array_values($rows) as $index => $row) {
        $label = 'Devisenkurs ' . ($index + 1);
        if (!is_array($row) || count($row) < 2) {
            $errors[] = "$label: erwartet [datum, kurs]";
            continue;
        }
        if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', (string)$row[0])) {
            $errors[] = "$label: '{$row[0]}' ist kein ISO-Datum";
        }
        if (!is_numeric($row[1])) {
            $errors[] = "$label: Kurs ist keine Zahl";
            continue;
        }
        $rate = (float)$row[1];
        if ($rate < FX_MIN_RATE || $rate > FX_MAX_RATE) {
            $errors[] = sprintf('%s: EUR/USD %.4f liegt ausserhalb von %.1f..%.1f'
                . ' — vermutlich die falsche Notierung',
                $label, $rate, FX_MIN_RATE, FX_MAX_RATE);
        }
    }
    return $errors;
}

/**
 * @param array $bars Optional: [ticker => [[datum, open, high, low, close, volume], ...]].
 *                    Fehlen sie, entfaellt der Abgleich des Entrys gegen den
 *                    letzten Schlusskurs -- ein fehlender Kurs darf keinen
 *                    Report blockieren.
 */
function validate_report(array $doc, array $bars = []): array {
    $errors = [];

    foreach (DOCUMENT_FIELDS as $field) {
        if (empty($doc[$field])) {
            $errors[] = "Dokument: Feld '$field' fehlt oder ist leer";
        }
    }
    if ($errors) {
        return $errors;
    }
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', (string)$doc['date'])) {
        $errors[] = "Dokument: date '{$doc['date']}' ist kein ISO-Datum";
    }
    if (!is_array($doc['picks'])) {
        return array_merge($errors, ['Dokument: picks ist keine Liste']);
    }
    if (count($doc['picks']) > MAX_PICKS) {
        $errors[] = 'Report enthaelt ' . count($doc['picks'])
            . ' Titel — mehr als erlaubte ' . MAX_PICKS;
    }

    $sectors = [];
    foreach (array_values($doc['picks']) as $index => $pick) {
        if (!is_array($pick)) {
            $errors[] = 'Pick ' . ($index + 1) . ' ist kein Objekt';
            continue;
        }
        $sector = $pick['sector'] ?? 'Unknown';
        $sectors[$sector] = ($sectors[$sector] ?? 0) + 1;
        $close = last_close($bars[$pick['ticker'] ?? ''] ?? [], (string)$doc['date']);
        $errors = array_merge($errors, validate_pick($pick, $index + 1, $close));
    }
    foreach ($sectors as $sector => $count) {
        if ($count > MAX_PER_SECTOR) {
            $errors[] = "Sektor '$sector' mit $count Titeln — erlaubt sind " . MAX_PER_SECTOR;
        }
    }
    return $errors;
}

/**
 * Letzter Schlusskurs bis einschliesslich $date, oder null. Die Bars werden
 * nicht als sortiert vorausgesetzt: gesucht ist die Bar mit dem groessten
 * Datum, das nicht hinter dem Report-Datum liegt. Eine Bar nach dem
 * Report-Datum ist keine Referenz -- sie gab es beim Schreiben noch nicht.
 */
function last_close(array $rows, string $date): ?float {
    $best = null;
    foreach ($rows as $row) {
        if (!is_array($row) || count($row) < 5 || !is_numeric($row[4])) {
            continue;
        }
        $bar_date = (string)$row[0];
        if ($bar_date > $date) {
            continue;
        }
        if ($best === null || $bar_date > $best[0]) {
            $best = [$bar_date, (float)$row[4]];
        }
    }
    return $best === null ? null : $best[1];
}

function validate_pick(array $pick, int $index, ?float $close = null): array {
    $label = $pick['ticker'] ?? "Pick $index";
    $errors = [];

    // Vollstaendigkeit zuerst: das Portal rendert jedes dieser Felder.
    foreach (REQUIRED_PICK_FIELDS as $field) {
        if (!array_key_exists($field, $pick)) {
            $errors[] = "$label: Feld '$field' fehlt";
        }
    }

    $levels_ok = true;
    foreach (['entry', 'stop', 'target1', 'target2'] as $field) {
        if (!isset($pick[$field]) || !is_numeric($pick[$field]) || $pick[$field] <= 0) {
            $errors[] = "$label: $field fehlt oder ist nicht positiv";
            $levels_ok = false;
        }
    }

    if ($levels_ok) {
        [$entry, $stop, $t1, $t2] = [(float)$pick['entry'], (float)$pick['stop'],
                                     (float)$pick['target1'], (float)$pick['target2']];

        if ($stop >= $entry) {
            $errors[] = "$label: Stop $stop liegt nicht unter dem Entry $entry";
        }
        if ($t1 <= $entry) {
            $errors[] = "$label: Target 1 $t1 liegt nicht ueber dem Entry $entry";
        }
        if ($t2 <= $t1) {
            $errors[] = "$label: Target 2 $t2 liegt nicht ueber Target 1 $t1";
        }
        if ($stop < $entry) {
            $rr = ($t1 - $entry) / ($entry - $stop);
            if ($rr < MIN_RISK_REWARD) {
                $errors[] = sprintf('%s: R:R %.2f unter dem Minimum %.1f',
                    $label, $rr, MIN_RISK_REWARD);
            }
        }

        if ($close) {
            $deviation = abs(100.0 * ($entry - $close) / $close);
            if ($deviation > MAX_ENTRY_DEVIATION_PCT) {
                $errors[] = sprintf(
                    '%s: Entry %s weicht %.1f%% vom letzten Schlusskurs %s ab'
                    . ' — vermutlich halluziniert',
                    $label, $entry, $deviation, $close);
            }
        }
    }

    if ((int)($pick['composite'] ?? 0) < MIN_COMPOSITE) {
        $errors[] = "$label: Composite " . ($pick['composite'] ?? 0)
            . ' unter Schwelle ' . MIN_COMPOSITE;
    }

    foreach (REQUIRED_PROSE as $field) {
        if (array_key_exists($field, $pick)
            && (!is_string($pick[$field]) || trim($pick[$field]) === '')) {
            $errors[] = "$label: Feld '$field' ist leer";
        }
    }

    $rationale = $pick['score_rationale'] ?? null;
    if (is_array($rationale)) {
        foreach (RATIONALE_KEYS as $key) {
            if (trim((string)($rationale[$key] ?? '')) === '') {
                $errors[] = "$label: score_rationale.$key fehlt";
            }
        }
    } elseif (array_key_exists('score_rationale', $pick)) {
        $errors[] = "$label: score_rationale ist kein Objekt";
    }

    $plan = $pick['plan'] ?? null;
    if (is_array($plan)) {
        foreach (PLAN_KEYS as $key) {
            if (trim((string)($plan[$key] ?? '')) === '') {
                $errors[] = "$label: plan.$key fehlt";
            }
        }
    } elseif (array_key_exists('plan', $pick)) {
        $errors[] = "$label: plan ist kein Objekt";
    }

    $sources = $pick['sources'] ?? [];
    if (!$sources || !is_array($sources)) {
        $errors[] = "$label: keine Quellen angegeben";
        return $errors;
    }

    // Objekte mit url-Feld, dazu nackte URL-Strings aus der Zeit vor dem Portal.
    $urls = [];
    foreach ($sources as $source) {
        if (is_string($source)) {
            $urls[] = $source;
        } elseif (is_array($source) && !empty($source['url'])) {
            $urls[] = (string)$source['url'];
        }
    }
    if (!$urls) {
        $errors[] = "$label: sources ohne 'url'-Feld";
    }
    foreach ($urls as $url) {
        if (!preg_match('#^https?://#i', $url)) {
            $errors[] = "$label: sources enthaelt keine brauchbare URL ($url)";
        }
    }
    return $errors;
}
