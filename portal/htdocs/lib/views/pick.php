<?php
/**
 * Pick-Detail: links Chart, R-Leiter, Scores, Kennzahlen und Handelsplan,
 * rechts die Begruendungen. Erwartet $pick (aus get_pick(), also inklusive
 * $pick['bars'], $pick['fx'] und mit gerechnetem risk_reward/position_size)
 * und $account.
 *
 * Angezeigt wird in EUR -- das ist die Waehrung des Depots und die Zahl, die
 * im Orderfenster eines Euro-Brokers steht. Gerechnet wird unveraendert in
 * USD: Chart, Leiter und Handelsplan zeigen dieselben USD-Niveaus, geteilt
 * durch den Devisenkurs des Reporttages. Der USD-Wert steht klein daneben,
 * weil die Pipeline, die Kennzahlen und jede Kontrolle gegen yfinance in USD
 * sprechen. Ohne Kurs bleibt die Anzeige bei USD.
 *
 * Jedes Feld aus der Datenbank stammt mittelbar aus einem Sprachmodell und
 * laeuft deshalb durch e() oder md() -- ohne Ausnahme.
 */

/**
 * Mindestkriterien der Setup-Templates, gespiegelt aus
 * stockpicker/templates.py (is_vcp_breakout, is_trend_pullback,
 * is_mean_reversion, is_pead). Nur die Kriterien, die auch im
 * Kennzahlenstreifen stehen; die uebrigen (base_length, base_depth_pct,
 * range_contraction, volume_dryup, rsi2, percent_b, days_since_earnings,
 * gap_low_held) haben dort keinen Balken.
 *
 * Die Vergleichsoperatoren sind bewusst exakt uebernommen -- templates.py
 * prueft rs_percentile mit >=, rvol dagegen strikt mit < beziehungsweise >.
 * Weicht hier etwas ab, zeigt das Portal ein Kriterium als erfuellt an, das
 * der Screener verworfen haette.
 */
const TEMPLATE_THRESHOLDS = [
    'VCP_BREAKOUT' => [
        'rs_percentile'         => ['gte', 80.0],
        'distance_to_pivot_pct' => ['between', -5.0, 1.0],
    ],
    'TREND_PULLBACK' => [
        'rs_percentile' => ['gte', 60.0],
        'rvol'          => ['lt', 1.0],
    ],
    'MEAN_REVERSION' => [
        // rsi2 < 10 und percent_b < 0.1 -- beides steht nicht im Streifen.
    ],
    'PEAD' => [
        'gap_pct' => ['gt', 5.0],
        'rvol'    => ['gt', 3.0],
    ],
];

/**
 * Der Kennzahlenstreifen. Die Skalen sind Anzeigebereiche, keine Schwellen:
 * sie legen nur fest, wo ein Wert auf dem Balken sitzt. Werte ausserhalb
 * werden auf die Enden geklemmt, damit ein Ausreisser den Massstab nicht
 * unlesbar macht.
 */
const METRIC_STRIP = [
    ['rs_percentile',         'RS-Perzentil',     0.0, 100.0, '',  0],
    ['rsi14',                 'RSI(14)',          0.0, 100.0, '',  1],
    ['rvol',                  'RVOL',             0.0,   6.0, '×', 2],
    ['atr_pct',               'ATR %',            0.0,  15.0, ' %', 2],
    ['gap_pct',               'Gap %',          -10.0,  20.0, ' %', 2],
    ['distance_to_pivot_pct', 'Abstand Pivot',  -20.0,   5.0, ' %', 2],
];

/** Position eines Werts auf einer Skala, in Prozent und auf 0..100 geklemmt. */
function scale_pos(float $value, float $min, float $max): float {
    if ($max <= $min) {
        return 0.0;
    }
    return max(0.0, min(100.0, 100.0 * ($value - $min) / ($max - $min)));
}

/** Prueft einen Messwert gegen die Template-Regel. Null heisst: keine Regel. */
function threshold_met(?array $rule, float $value): ?bool {
    if (!$rule) {
        return null;
    }
    return match ($rule[0]) {
        'gte'     => $value >= $rule[1],
        'gt'      => $value >  $rule[1],
        'lt'      => $value <  $rule[1],
        'between' => $value >= $rule[1] && $value <= $rule[2],
        default   => null,
    };
}

$metrics   = is_array($pick['metrics'] ?? null) ? $pick['metrics'] : [];
$rationale = is_array($pick['score_rationale'] ?? null) ? $pick['score_rationale'] : [];
$plan      = is_array($pick['plan'] ?? null) ? $pick['plan'] : [];
$sources   = is_array($pick['sources'] ?? null) ? $pick['sources'] : [];
$bars      = is_array($pick['bars'] ?? null) ? $pick['bars'] : [];
$rules     = TEMPLATE_THRESHOLDS[(string)$pick['setup']] ?? [];

// Der Devisenkurs des Reporttages, nicht der von heute: die EUR-Niveaus
// eines abgeschlossenen Trades duerfen sich nachtraeglich nicht mehr aendern.
$rate    = $pick['fx']['rate'] ?? null;
$fx_date = $pick['fx']['date'] ?? null;

// Anzeigewaehrung. $disp rechnet eine Zahl fuer Chart und Leiter um, $price
// setzt sie mit Waehrungszeichen. Ohne Kurs geben beide den USD-Wert zurueck
// -- eine unumgerechnete Zahl mit einem Eurozeichen daneben waere die
// teuerste Art, hier zu scheitern.
$disp  = fn(?float $usd): ?float => $rate === null ? $usd : to_eur($usd, $rate);
$price = fn(?float $usd): string => $rate === null ? usd($usd) : eur(to_eur($usd, $rate));

$entry = is_numeric($pick['entry'] ?? null) ? (float)$pick['entry'] : null;
$stop  = is_numeric($pick['stop'] ?? null) ? (float)$pick['stop'] : null;
$t1    = is_numeric($pick['target1'] ?? null) ? (float)$pick['target1'] : null;
$t2    = is_numeric($pick['target2'] ?? null) ? (float)$pick['target2'] : null;

// Letzter bekannter Schlusskurs. Ohne Bars gibt es keinen Zeiger auf der
// Leiter -- der Wert aus metrics['close'] stammt vom Screener-Tag und waere
// als "aktueller Kurs" schlicht falsch beschriftet.
$last_close = $bars ? (float)end($bars)['close'] : null;
$last_date  = $bars ? (string)end($bars)['date'] : null;

// Massstab der R-Leiter. Die Raender sind zwei Prozent Luft, damit Stop und
// Target nicht auf der Kante kleben.
$ladder = [];
if ($entry !== null && $stop !== null) {
    $low  = min($stop, $entry, $last_close ?? $entry) * 0.98;
    $high = max($t2 ?? $t1 ?? $entry, $entry, $last_close ?? $entry) * 1.02;
    if ($high > $low) {
        foreach ([
            ['Target 2', $t2, 'bg-green-400', 'text-green-500'],
            ['Target 1', $t1, 'bg-green-600', 'text-green-600 dark:text-green-400'],
            ['Entry',    $entry, 'bg-blue-500', 'text-blue-600 dark:text-blue-400'],
            ['Stop',     $stop, 'bg-red-600', 'text-red-600 dark:text-red-400'],
        ] as [$label, $level, $bar, $text]) {
            if ($level === null) {
                continue;
            }
            $ladder[] = ['label' => $label, 'price' => (float)$level,
                         'pos' => scale_pos((float)$level, $low, $high),
                         'bar' => $bar, 'text' => $text];
        }
    }
}

// Erreichte R-Zahl: wie viele Vielfache des Anfangsrisikos der Kurs seit dem
// Einstiegsniveau gelaufen ist.
$r_now = ($last_close !== null && $entry !== null && $stop !== null && $entry > $stop)
    ? ($last_close - $entry) / ($entry - $stop)
    : null;
$r_pos = ($last_close !== null && $ladder) ? scale_pos($last_close, $low, $high) : null;

$scores = [
    ['Technik',    (int)$pick['score_technical'], $rationale['technical'] ?? '', 'bg-blue-600'],
    ['Katalysator', (int)$pick['score_catalyst'], $rationale['catalyst'] ?? '', 'bg-purple-600'],
    ['Risiko',      (int)$pick['score_risk'],     $rationale['risk'] ?? '',     'bg-amber-500'],
];

// Der Chart traegt dieselbe Waehrung wie die Beschriftung neben ihm: die
// Preisachse steht sonst in USD, waehrend Entry und Stop daneben in EUR
// stehen. Die Umrechnung ist eine Streckung mit einem festen Faktor -- die
// Form der Kerzen, die Marker und die R-Leiter bleiben davon unberuehrt.
// Das Volumen zaehlt Stuecke und wird deshalb nicht umgerechnet.
$chart_data = json_encode([
    'bars'        => array_map(fn(array $bar): array => [
        'date'   => (string)$bar['date'],
        'open'   => (float)$disp((float)$bar['open']),
        'high'   => (float)$disp((float)$bar['high']),
        'low'    => (float)$disp((float)$bar['low']),
        'close'  => (float)$disp((float)$bar['close']),
        'volume' => (float)$bar['volume'],
    ], $bars),
    'entry'       => $disp($entry),
    'stop'        => $disp($stop),
    'target1'     => $disp($t1),
    'target2'     => $disp($t2),
    'pickDate'    => (string)$pick['report_date'],
    'composite'   => (int)$pick['composite'],
    'triggeredOn' => $pick['triggered_on'] ?? null,
    'closedOn'    => $pick['closed_on'] ?? null,
    'status'      => (string)$pick['status'],
    'earnings'    => $metrics['next_earnings'] ?? null,
], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
?>
<nav class="mb-4 text-sm text-gray-500 dark:text-gray-400">
  <a href="/" class="hover:underline">Reports</a>
  <span class="mx-1">/</span>
  <a href="/r/<?= e(rawurlencode((string)$pick['report_date'])) ?>" class="hover:underline"><?= e((string)$pick['report_date']) ?></a>
  <span class="mx-1">/</span>
  <span class="text-gray-900 dark:text-white"><?= e((string)$pick['ticker']) ?></span>
</nav>

<div class="grid grid-cols-1 lg:grid-cols-5 gap-6">

  <div class="lg:col-span-3 lg:sticky lg:top-4 self-start space-y-6">

    <header>
      <div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white"><?= e((string)$pick['ticker']) ?></h1>
        <span class="text-lg text-gray-700 dark:text-gray-300"><?= e((string)$pick['name']) ?></span>
      </div>
      <div class="mt-2 flex flex-wrap items-center gap-2">
        <span class="text-xs font-medium me-2 px-2.5 py-0.5 rounded-sm bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"><?= e((string)$pick['sector']) ?></span>
        <span class="text-xs font-medium me-2 px-2.5 py-0.5 rounded-sm bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-300"><?= e((string)$pick['setup']) ?></span>
        <?= status_badge($pick['status'] ?? null) ?>
        <span class="text-xs text-gray-500 dark:text-gray-400">Rang <?= (int)$pick['rank'] ?></span>
      </div>
    </header>

    <section class="flex gap-3">
      <div class="min-w-0 flex-1">
<?php if ($bars): ?>
        <div id="chart"
             class="h-[420px] w-full rounded-lg border border-gray-200 dark:border-gray-700"
             data-chart='<?= e($chart_data) ?>'></div>
<?php else: ?>
        <!-- Ohne Bars steht hier der Hinweis, nicht der leere Rahmen: eine
             leere Flaeche in Chartgroesse sieht wie ein Fehler aus. -->
        <div class="flex h-[420px] w-full items-center justify-center rounded-lg border border-dashed border-gray-300 dark:border-gray-600">
          <p class="max-w-xs p-6 text-center text-sm text-gray-500 dark:text-gray-400">
            Für diesen Titel liegen keine Kursdaten vor.
            Der nächste <code class="rounded bg-gray-100 px-1 dark:bg-gray-700">publish.py --sync</code>
            liefert sie nach.
          </p>
        </div>
<?php endif; ?>
      </div>

<?php if ($ladder): ?>
      <div class="shrink-0">
        <!-- data-chart-ladder verweist auf die id des Charts. chart.js setzt
             die Sprossen danach auf die Koordinaten der Preisskala um; die
             Prozentwerte hier sind nur der Stand ohne JavaScript. -->
        <div class="relative h-[420px] w-24" data-chart-ladder="chart">
<?php foreach ($ladder as $rung): ?>
          <!-- Beschriftung einzeilig: auf der Skala des Charts liegen Entry,
               letzter Kurs und Stop dicht beieinander, und zwei Textzeilen je
               Sprosse ueberlagern sich dort. -->
          <div class="absolute inset-x-0"
               data-price="<?= number_format((float)$disp($rung['price']), 4, '.', '') ?>"
               style="bottom: <?= number_format($rung['pos'], 2, '.', '') ?>%">
            <div data-rung-label class="flex items-baseline justify-between gap-1 text-[10px] leading-tight">
              <span class="<?= $rung['text'] ?>"><?= e($rung['label']) ?></span>
              <span class="text-gray-500 dark:text-gray-400"><?= $price($rung['price']) ?></span>
            </div>
            <div class="h-0.5 w-full <?= $rung['bar'] ?>"></div>
          </div>
<?php endforeach; ?>
<?php if ($r_pos !== null): ?>
          <!-- Vorrang vor den festen Niveaus: die R-Zahl ist das Einzige, was
               die Leiter zeigt und der Chart nicht -- Entry, Stop und die
               Targets stehen dort ohnehin an der Preisachse. -->
          <div class="absolute inset-x-0" data-rung-priority="2"
               data-price="<?= number_format((float)$disp((float)$last_close), 4, '.', '') ?>"
               style="bottom: <?= number_format($r_pos, 2, '.', '') ?>%">
            <div data-rung-label class="flex items-baseline justify-between gap-1 text-[10px] leading-tight font-semibold text-gray-900 dark:text-white">
              <span>Kurs</span>
              <span><?= $r_now === null ? '—' : money($r_now, 2) . ' R' ?></span>
            </div>
            <div class="h-px w-full bg-gray-900 dark:bg-white"></div>
          </div>
<?php endif; ?>
        </div>
<?php if ($last_date !== null): ?>
        <p class="mt-1 text-[10px] text-gray-500 dark:text-gray-400">Stand<br><?= e($last_date) ?></p>
<?php endif; ?>
      </div>
<?php endif; ?>
    </section>

    <section class="p-4 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
      <div class="flex items-start gap-6">
        <div class="min-w-0 flex-1 space-y-4">
<?php foreach ($scores as [$label, $value, $why, $color]): ?>
          <div>
            <div class="flex justify-between mb-1 text-sm font-medium text-gray-700 dark:text-gray-300">
              <span><?= e($label) ?></span>
              <span><?= (int)$value ?> / 10</span>
            </div>
            <div class="w-full bg-gray-200 rounded-full h-2 dark:bg-gray-700">
              <div class="<?= $color ?> h-2 rounded-full" style="width: <?= number_format(scale_pos((float)$value, 0.0, 10.0), 2, '.', '') ?>%"></div>
            </div>
<?php if (trim((string)$why) !== ''): ?>
            <p class="mt-1 text-sm text-gray-600 dark:text-gray-400"><?= e((string)$why) ?></p>
<?php endif; ?>
          </div>
<?php endforeach; ?>
        </div>
        <div class="shrink-0 text-center">
          <div class="text-4xl font-bold text-gray-900 dark:text-white"><?= (int)$pick['composite'] ?></div>
          <div class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Composite</div>
        </div>
      </div>
    </section>

<?php if ($metrics): ?>
    <section class="p-4 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
        Kennzahlen <span class="normal-case font-normal">· Schwellen aus <?= e((string)$pick['setup']) ?></span>
      </h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
<?php foreach (METRIC_STRIP as [$key, $label, $min, $max, $unit, $decimals]): ?>
<?php
        if (!isset($metrics[$key]) || !is_numeric($metrics[$key])) {
            continue;
        }
        $value = (float)$metrics[$key];
        $rule  = $rules[$key] ?? null;
        $met   = threshold_met($rule, $value);
        $marks = [];
        if ($rule) {
            $marks[] = scale_pos((float)$rule[1], $min, $max);
            if ($rule[0] === 'between') {
                $marks[] = scale_pos((float)$rule[2], $min, $max);
            }
        }
        $fill = $met === null ? 'bg-gray-400 dark:bg-gray-500'
              : ($met ? 'bg-green-600' : 'bg-red-500');
?>
        <div>
          <div class="flex justify-between text-xs text-gray-600 dark:text-gray-400">
            <span><?= e($label) ?></span>
            <span class="font-medium text-gray-900 dark:text-white"><?= money($value, $decimals) . e($unit) ?></span>
          </div>
          <div class="relative mt-1 h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700">
            <div class="absolute inset-y-0 left-0 rounded-full <?= $fill ?>"
                 style="width: <?= number_format(scale_pos($value, $min, $max), 2, '.', '') ?>%"></div>
<?php foreach ($marks as $mark): ?>
            <div class="absolute -top-0.5 h-2.5 w-0.5 bg-gray-900 dark:bg-white"
                 style="left: <?= number_format($mark, 2, '.', '') ?>%"></div>
<?php endforeach; ?>
          </div>
        </div>
<?php endforeach; ?>
      </div>
<?php if (!$rules): ?>
      <p class="mt-3 text-xs text-gray-500 dark:text-gray-400">
        Für dieses Setup hinterlegt <code>stockpicker/templates.py</code> keine Schwelle
        auf diesen Kennzahlen — die Balken zeigen nur den Wert.
      </p>
<?php endif; ?>
    </section>
<?php endif; ?>

    <section class="p-4 bg-white border border-gray-200 rounded-lg shadow-sm dark:bg-gray-800 dark:border-gray-700">
      <h2 class="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Handelsplan</h2>
      <!-- Die Order laeuft in EUR, also fuehrt der EUR-Betrag. Der USD-Wert
           steht klein darunter statt gar nicht: er ist die Zahl, die in der
           Pipeline, im Ledger und bei jeder Kontrolle gegen yfinance steht,
           und zwei Massstaebe ohne Kennzeichnung waeren die teurere
           Verwechslung. -->
      <dl class="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
<?php foreach ([
        ['Entry',    $entry, $plan['entry'] ?? ''],
        ['Stop',     $stop,  $plan['stop'] ?? ''],
        ['Target 1', $t1,    $plan['target1'] ?? ''],
        ['Target 2', $t2,    $plan['target2'] ?? ''],
      ] as [$label, $level, $note]): ?>
        <div>
          <dt class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400"><?= e($label) ?></dt>
          <dd class="font-semibold text-gray-900 dark:text-white"><?= $price($level === null ? null : (float)$level) ?></dd>
<?php if ($rate !== null): ?>
          <dd class="text-xs text-gray-600 dark:text-gray-400"><?= usd($level === null ? null : (float)$level) ?></dd>
<?php endif; ?>
<?php if (trim((string)$note) !== ''): ?>
          <dd class="text-gray-600 dark:text-gray-400"><?= e((string)$note) ?></dd>
<?php endif; ?>
        </div>
<?php endforeach; ?>
        <div>
          <dt class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Chance/Risiko</dt>
          <dd class="font-semibold text-gray-900 dark:text-white">
            <?= $pick['risk_reward'] === null ? '—' : money((float)$pick['risk_reward'], 2) . ' : 1' ?>
          </dd>
        </div>
        <div>
          <dt class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
            Position bei <?= money(ACCOUNT_RISK_PCT, 0) ?> % von <?= eur($account, 0) ?>
          </dt>
          <dd class="font-semibold text-gray-900 dark:text-white">
            <?= $pick['position_size'] === null ? '—' : money((float)$pick['position_size'], 0) . ' Stück' ?>
          </dd>
<?php if ($pick['position_size'] !== null && $entry !== null && $stop !== null): ?>
          <dd class="text-xs text-gray-600 dark:text-gray-400">
            Risiko <?= eur(to_eur(($entry - $stop) * (int)$pick['position_size'], $rate)) ?>
          </dd>
<?php endif; ?>
        </div>
      </dl>
<?php if ($rate !== null): ?>
      <p class="mt-3 text-xs text-gray-500 dark:text-gray-400">
        Alle Beträge in EUR, umgerechnet mit EUR/USD <?= money($rate, 4) ?>
        vom <?= e((string)$fx_date) ?>; darunter steht der USD-Wert, in dem die
        Pipeline rechnet. Ein Broker rechnet zum Kurs der Ausführung ab und
        nimmt eine Marge — die EUR-Beträge sind Orientierung, keine Abrechnung.
      </p>
<?php else: ?>
      <p class="mt-3 text-xs text-amber-700 dark:text-amber-400">
        Kein EUR/USD-Kurs im Portal — Chart und Niveaus stehen deshalb in USD
        und die Stückzahl bleibt leer. Der nächste
        <code class="rounded bg-gray-100 px-1 dark:bg-gray-700">publish.py --sync</code>
        liefert den Kurs nach.
      </p>
<?php endif; ?>
    </section>

  </div>

  <div class="lg:col-span-2 space-y-6">

    <section>
      <h2 class="mb-2 text-xl font-semibold text-gray-900 dark:text-white">These</h2>
      <div class="<?= prose_classes('text-sm text-gray-700 dark:text-gray-300') ?>"><?= md($pick['thesis'] ?? '') ?></div>
    </section>

    <section>
      <h2 class="mb-2 text-xl font-semibold text-gray-900 dark:text-white">Technische Lage</h2>
      <div class="<?= prose_classes('text-sm text-gray-700 dark:text-gray-300') ?>"><?= md($pick['technicals'] ?? '') ?></div>
    </section>

    <section>
      <h2 class="mb-2 text-xl font-semibold text-gray-900 dark:text-white">Katalysator</h2>
<?php if (trim((string)($pick['catalyst'] ?? '')) !== ''): ?>
      <p class="mb-2 text-sm font-medium text-gray-900 dark:text-white"><?= e((string)$pick['catalyst']) ?></p>
<?php endif; ?>
      <div class="<?= prose_classes('text-sm text-gray-700 dark:text-gray-300') ?>"><?= md($pick['catalyst_detail'] ?? '') ?></div>
    </section>

    <section class="rounded-lg border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-950/30 p-4">
      <h2 class="mb-2 text-lg font-semibold text-amber-900 dark:text-amber-200">Bear-Case</h2>
      <div class="<?= prose_classes('text-sm text-amber-900 dark:text-amber-100') ?>"><?= md($pick['bear_case'] ?? '') ?></div>
    </section>

    <section class="rounded-lg border-l-4 border-red-500 bg-red-50 dark:bg-red-950/30 p-4">
      <h2 class="mb-2 text-lg font-semibold text-red-900 dark:text-red-200">Invalidierung</h2>
      <div class="<?= prose_classes('text-sm text-red-900 dark:text-red-100') ?>"><?= md($pick['invalidation'] ?? '') ?></div>
    </section>

    <section>
      <h2 class="mb-2 text-xl font-semibold text-gray-900 dark:text-white">Quellen</h2>
<?php if (!$sources): ?>
      <p class="text-sm text-gray-500 dark:text-gray-400">Keine Quellen angegeben.</p>
<?php else: ?>
      <ul class="space-y-1 text-sm">
<?php foreach ($sources as $source): ?>
<?php
        $url = (string)($source['url'] ?? '');
        // Nur http(s) wird verlinkt: javascript:- und data:-URLs aus
        // Modell-Ausgabe haetten hier sonst ein Ziel.
        $linkable = (bool)preg_match('~\A https?://~ix', $url);
        // Nicht $title nennen: die View laeuft im Scope von render(), und
        // dort ist $title der Seitentitel, den layout.php danach ausgibt.
        $source_title = trim((string)($source['title'] ?? ''));
        if ($source_title === '') {
            $source_title = (string)(parse_url($url, PHP_URL_HOST) ?: $url);
        }
        $date = trim((string)($source['date'] ?? ''));
?>
        <li class="flex flex-wrap gap-x-2">
<?php if ($linkable): ?>
          <a href="<?= e($url) ?>" target="_blank" rel="noopener noreferrer"
             class="text-blue-600 hover:underline dark:text-blue-400"><?= e($source_title) ?></a>
<?php else: ?>
          <span class="text-gray-700 dark:text-gray-300"><?= e($source_title) ?></span>
<?php endif; ?>
<?php if ($date !== ''): ?>
          <span class="text-gray-500 dark:text-gray-400"><?= e($date) ?></span>
<?php endif; ?>
        </li>
<?php endforeach; ?>
      </ul>
<?php endif; ?>
    </section>

  </div>
</div>
