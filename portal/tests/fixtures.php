<?php
// Testdaten fuer die Portal-Tests. Ein Dokument, das validate_report()
// fehlerfrei passiert: zwei Picks aus verschiedenen Sektoren (damit die
// Grenze von zwei Titeln je Sektor nicht greift) und je fuenf Bars.

function fixture_pick(array $overrides = []): array {
    $pick = [
        'ticker' => 'XYZ', 'name' => 'Example Corp', 'sector' => 'Health Care',
        'setup' => 'VCP_BREAKOUT', 'rank' => 1,
        'score_technical' => 8, 'score_catalyst' => 7, 'score_risk' => 7,
        'composite' => 76,
        'score_rationale' => [
            'technical' => 'Enge Kontraktion ueber der 50-Tage-Linie',
            'catalyst'  => 'Zulassungsentscheidung steht an',
            'risk'      => 'Stop knapp unter dem Ausbruchsniveau',
        ],
        'entry' => 42.80, 'stop' => 40.10, 'target1' => 48.20, 'target2' => 52.00,
        'risk_reward' => 2.0, 'position_size' => 37,
        'catalyst' => 'FDA-Zulassung 2026-08-06',
        'thesis' => 'Ausbruch aus mehrmonatiger Basis mit steigendem Volumen.',
        'technicals' => 'Relative Staerke im oberen Dezil, Volumen ueber Schnitt.',
        'catalyst_detail' => 'Entscheidung der Behoerde fuer den 06.08. angesetzt.',
        'plan' => [
            'entry' => 'Kauf ueber 42.80 mit Volumenbestaetigung',
            'stop' => 'Ausstieg unter 40.10 auf Schlusskursbasis',
            'target1' => 'Erste Teilposition bei 48.20',
            'target2' => 'Rest bei 52.00',
        ],
        'bear_case' => 'Ablehnung der Zulassung fuehrt zu einem Gap nach unten.',
        'invalidation' => 'Schlusskurs unter 40.10 beendet das Setup.',
        'metrics' => ['close' => 42.15, 'atr14' => 1.85],
        'sources' => [
            ['url' => 'https://example.com/xyz', 'title' => 'Example', 'date' => '2026-08-06'],
        ],
    ];
    return array_merge($pick, $overrides);
}

function fixture_bars(float $base): array {
    $dates = ['2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07'];
    $bars = [];
    foreach ($dates as $offset => $date) {
        $open = round($base + $offset * 0.30, 2);
        $bars[] = [
            $date,
            $open,
            round($open + 0.60, 2),
            round($open - 0.45, 2),
            round($open + 0.25, 2),
            1200000.0 + $offset * 50000.0,
        ];
    }
    return $bars;
}

function fixture_document(array $overrides = []): array {
    $doc = [
        'date' => '2026-08-09',
        'generated_at' => '2026-08-09T12:35:34',
        'market_context' => 'Breite Indizes ueber der 50-Tage-Linie, Risikoneigung intakt.',
        'note' => 'Zwei Titel erfuellen alle Kriterien.',
        'picks' => [
            fixture_pick(),
            fixture_pick([
                'ticker' => 'ABC', 'name' => 'Acme Industries',
                'sector' => 'Information Technology', 'rank' => 2,
                'setup' => 'FLAT_BASE',
                'score_technical' => 7, 'score_catalyst' => 7, 'score_risk' => 8,
                'composite' => 71,
                'entry' => 30.00, 'stop' => 28.00, 'target1' => 34.50, 'target2' => 38.00,
                'risk_reward' => 2.25, 'position_size' => 50,
                'catalyst' => 'Quartalszahlen 2026-08-11',
                'metrics' => ['close' => 29.80, 'atr14' => 0.92],
                'sources' => [
                    ['url' => 'https://example.com/abc', 'title' => 'Acme', 'date' => '2026-08-07'],
                ],
            ]),
        ],
        'rejected' => [['ticker' => 'DEF', 'reason' => 'Earnings in drei Tagen']],
        'checklist' => ['Jede Zahl traegt Screener oder Quelle'],
        'disclaimer' => 'Keine Anlageberatung.',
        'bars' => [
            'XYZ' => fixture_bars(41.50),
            'ABC' => fixture_bars(29.00),
        ],
    ];
    return array_merge($doc, $overrides);
}
