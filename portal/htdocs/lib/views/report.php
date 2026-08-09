<?php
/**
 * Ein Report: Kopf, Marktkontext, Uebersichtstabelle, je Pick eine Karte,
 * verworfene Kandidaten, Checkliste, Disclaimer.
 * Erwartet $report aus get_report().
 *
 * Jedes Feld stammt mittelbar aus einem Sprachmodell und geht deshalb
 * entweder durch md() (Prosa) oder durch e() (alles andere). Zahlen laufen
 * durch money(), damit Komma und Tausenderpunkt ueberall gleich aussehen.
 */

$date      = (string)$report['date'];
$picks     = $report['picks'] ?? [];
$rejected  = array_values($report['rejected'] ?? []);
$checklist = array_values($report['checklist'] ?? []);

// Wiederkehrende Klassenketten einmal benannt, damit die Seiten des Portals
// dieselben Abstaende und Farben tragen wie die Uebersicht.
$card = 'p-6 bg-white border border-gray-200 rounded-lg shadow-sm '
      . 'dark:bg-gray-800 dark:border-gray-700';
$h2   = 'mb-3 text-xl font-semibold tracking-tight text-gray-900 dark:text-white';
$cell = 'px-4 py-3 whitespace-nowrap';

// Prosa-Klassen kommen aus markdown.php, gleich neben md(). Hier steht nur,
// was zu dieser Seite gehoert: Textfarbe und die kraeftigere Auszeichnung
// von <strong> auf hellem Kartengrund.
$prose = prose_classes('text-gray-700 dark:text-gray-300 '
       . '[&_strong]:text-gray-900 dark:[&_strong]:text-white');

/** Zeitstempel wie in der Uebersicht: Datum und Uhrzeit, keine Sekunden. */
$stamp = static fn(?string $value): string => $value
    ? str_replace('T', ' ', substr($value, 0, 16)) : '—';

$pick_url = static fn(string $ticker): string =>
    '/r/' . rawurlencode($date) . '/' . rawurlencode($ticker);

$scores = [
    ['technical', 'Technik',      'bg-blue-600 dark:bg-blue-500',      'score_technical'],
    ['catalyst',  'Katalysator',  'bg-purple-600 dark:bg-purple-500',  'score_catalyst'],
    ['risk',      'Risiko',       'bg-emerald-600 dark:bg-emerald-500', 'score_risk'],
];
?>
<header class="mb-8">
  <h1 class="text-3xl font-bold tracking-tight text-gray-900 dark:text-white">
    Report <?= e($date) ?>
  </h1>
  <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
    Erstellt <?= e($stamp($report['generated_at'] ?? null)) ?>
    · übergeben <?= e($stamp($report['pushed_at'] ?? null)) ?>
    · <?= count($picks) ?> <?= count($picks) === 1 ? 'Pick' : 'Picks' ?>
  </p>
</header>

<?php if (trim((string)($report['market_context'] ?? '')) !== ''): ?>
<section class="mb-8">
  <h2 class="<?= $h2 ?>">Marktkontext</h2>
  <div class="<?= $card ?>">
    <div class="<?= $prose ?>"><?= md($report['market_context']) ?></div>
  </div>
</section>
<?php endif; ?>

<?php if (trim((string)($report['note'] ?? '')) !== ''): ?>
<section class="mb-8">
  <div class="rounded-lg border-l-4 border-blue-500 bg-blue-50 p-4 dark:bg-blue-950/30">
    <h2 class="mb-2 text-sm font-semibold uppercase tracking-wide text-blue-800 dark:text-blue-300">
      Notiz zum Lauf
    </h2>
    <div class="<?= $prose ?>"><?= md($report['note']) ?></div>
  </div>
</section>
<?php endif; ?>

<?php if ($picks): ?>
<section class="mb-8">
  <h2 class="<?= $h2 ?>">Übersicht</h2>
  <div class="relative overflow-x-auto border border-gray-200 rounded-lg shadow-sm dark:border-gray-700">
    <table class="w-full text-sm text-left rtl:text-right text-gray-500 dark:text-gray-400">
      <thead class="text-xs uppercase text-gray-700 bg-gray-50 dark:bg-gray-700 dark:text-gray-400">
        <tr>
          <th scope="col" class="<?= $cell ?>">#</th>
          <th scope="col" class="<?= $cell ?>">Ticker</th>
          <th scope="col" class="<?= $cell ?>">Setup</th>
          <th scope="col" class="<?= $cell ?> text-right">Composite</th>
          <th scope="col" class="<?= $cell ?> text-right">Entry</th>
          <th scope="col" class="<?= $cell ?> text-right">Stop</th>
          <th scope="col" class="<?= $cell ?> text-right">Target 1</th>
          <th scope="col" class="<?= $cell ?> text-right">R:R</th>
          <th scope="col" class="<?= $cell ?>">Status</th>
          <th scope="col" class="<?= $cell ?>">Katalysator</th>
        </tr>
      </thead>
      <tbody>
<?php foreach ($picks as $pick): ?>
        <tr class="bg-white border-t border-gray-200 dark:bg-gray-800 dark:border-gray-700">
          <td class="<?= $cell ?>"><?= (int)$pick['rank'] ?></td>
          <th scope="row" class="<?= $cell ?> font-medium text-gray-900 dark:text-white">
            <a href="<?= e($pick_url((string)$pick['ticker'])) ?>" class="hover:underline">
              <?= e($pick['ticker']) ?>
            </a>
          </th>
          <td class="<?= $cell ?>"><?= e($pick['setup']) ?></td>
          <td class="<?= $cell ?> text-right font-medium text-gray-900 dark:text-white">
            <?= (int)$pick['composite'] ?>
          </td>
          <td class="<?= $cell ?> text-right"><?= e(money($pick['entry'])) ?></td>
          <td class="<?= $cell ?> text-right"><?= e(money($pick['stop'])) ?></td>
          <td class="<?= $cell ?> text-right"><?= e(money($pick['target1'])) ?></td>
          <td class="<?= $cell ?> text-right"><?= e(money($pick['risk_reward'])) ?></td>
          <td class="<?= $cell ?>"><?= status_badge($pick['status'] ?? null) ?></td>
          <td class="px-4 py-3 max-w-xs truncate" title="<?= e($pick['catalyst']) ?>">
            <?= e($pick['catalyst']) ?>
          </td>
        </tr>
<?php endforeach; ?>
      </tbody>
    </table>
  </div>
</section>

<section class="mb-8 space-y-4">
<?php foreach ($picks as $pick): ?>
<?php
    $ticker = (string)$pick['ticker'];
    $bars   = $pick['bars'] ?? [];
    // Dasselbe JSON, das die Detailseite an chart.js gibt; nur der Container
    // ist kleiner. Ein zweites Format waere ein zweiter Ort zum Pflegen.
    $chart = json_encode([
        'bars'        => $bars,
        'entry'       => (float)$pick['entry'],
        'stop'        => (float)$pick['stop'],
        'target1'     => (float)$pick['target1'],
        'target2'     => (float)$pick['target2'],
        'pickDate'    => $date,
        'composite'   => (int)$pick['composite'],
        'triggeredOn' => $pick['triggered_on'] ?? null,
        'closedOn'    => $pick['closed_on'] ?? null,
        'status'      => $pick['status'] ?? null,
        'earnings'    => $pick['metrics']['next_earnings'] ?? null,
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
?>
  <article class="<?= $card ?>">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 class="text-xl font-semibold text-gray-900 dark:text-white">
          <a href="<?= e($pick_url($ticker)) ?>" class="hover:underline"><?= e($ticker) ?></a>
          <span class="font-normal text-gray-500 dark:text-gray-400"><?= e($pick['name']) ?></span>
        </h3>
        <p class="mt-1 flex flex-wrap items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
          <span><?= e($pick['sector']) ?></span>
          <span class="text-xs font-medium px-2.5 py-0.5 rounded-sm bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300">
            <?= e($pick['setup']) ?>
          </span>
          <?= status_badge($pick['status'] ?? null) ?>
        </p>
      </div>
      <div class="text-right">
        <div class="text-3xl font-bold text-gray-900 dark:text-white"><?= (int)$pick['composite'] ?></div>
        <div class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Composite</div>
      </div>
    </div>

    <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
<?php if ($bars): ?>
      <div class="chart-mini h-44 w-full rounded-lg border border-gray-200 dark:border-gray-700"
           data-chart='<?= e($chart) ?>'></div>
<?php else: ?>
      <div class="flex h-44 w-full items-center justify-center rounded-lg border border-dashed border-gray-300 p-4 text-center text-sm text-gray-500 dark:border-gray-600 dark:text-gray-400">
        Keine Kursdaten. Der nächste <code>publish.py --sync</code> liefert sie nach.
      </div>
<?php endif; ?>

      <div class="space-y-3">
<?php foreach ($scores as [$key, $label, $bar, $column]): ?>
<?php $score = max(0, min(10, (int)($pick[$column] ?? 0))); ?>
        <div>
          <div class="flex items-baseline justify-between text-sm">
            <span class="font-medium text-gray-900 dark:text-white"><?= e($label) ?></span>
            <span class="text-gray-500 dark:text-gray-400"><?= $score ?>/10</span>
          </div>
          <div class="mt-1 w-full bg-gray-200 rounded-full h-2 dark:bg-gray-700">
            <div class="<?= $bar ?> h-2 rounded-full" style="width: <?= $score * 10 ?>%"></div>
          </div>
<?php if (trim((string)($pick['score_rationale'][$key] ?? '')) !== ''): ?>
          <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
            <?= e($pick['score_rationale'][$key]) ?>
          </p>
<?php endif; ?>
        </div>
<?php endforeach; ?>
      </div>
    </div>

    <dl class="mt-4 grid grid-cols-2 gap-3 border-t border-gray-200 pt-4 text-sm sm:grid-cols-6 dark:border-gray-700">
<?php
      $levels = [
          'Entry'     => money($pick['entry']),
          'Stop'      => money($pick['stop']),
          'Target 1'  => money($pick['target1']),
          'Target 2'  => money($pick['target2']),
          'R:R'       => money($pick['risk_reward']),
          'Stück'     => $pick['position_size'] === null ? '—' : money((float)$pick['position_size'], 0),
      ];
?>
<?php foreach ($levels as $label => $value): ?>
      <div>
        <dt class="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400"><?= e($label) ?></dt>
        <dd class="font-medium text-gray-900 dark:text-white"><?= e($value) ?></dd>
      </div>
<?php endforeach; ?>
    </dl>

    <p class="mt-4 text-sm">
      <a href="<?= e($pick_url($ticker)) ?>"
         class="font-medium text-blue-600 hover:underline dark:text-blue-400">
        Vollständige Analyse →
      </a>
    </p>
  </article>
<?php endforeach; ?>
</section>
<?php else: ?>
<div class="<?= $card ?> mb-8 text-gray-500 dark:text-gray-400">
  Dieser Report enthält keine Picks.
</div>
<?php endif; ?>

<?php
// Verworfene Kandidaten und Checkliste liest man einmal und dann nie wieder:
// zugeklappt, aber vorhanden. Fehlt beides, entfaellt das Akkordeon ganz --
// eine leere Ueberschrift ist schlechter als keine.
$panels = [];
if ($rejected) {
    $panels[] = ['id' => 'rejected', 'label' => 'Verworfene Kandidaten (' . count($rejected) . ')'];
}
if ($checklist) {
    $panels[] = ['id' => 'checklist', 'label' => 'Checkliste'];
}
?>
<?php if ($panels): ?>
<section class="mb-8" id="report-panels" data-accordion="collapse">
<?php foreach ($panels as $index => $panel): ?>
<?php
    $first = $index === 0;
    $last  = $index === count($panels) - 1;
?>
  <h2 id="<?= e($panel['id']) ?>-heading">
    <button type="button"
            data-accordion-target="#<?= e($panel['id']) ?>-body"
            aria-expanded="false"
            aria-controls="<?= e($panel['id']) ?>-body"
            class="flex items-center justify-between w-full gap-3 p-4 font-medium rtl:text-right text-gray-700 border border-gray-200 <?= $first ? 'rounded-t-lg' : 'border-t-0' ?> bg-white hover:bg-gray-100 dark:bg-gray-800 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-700">
      <span><?= e($panel['label']) ?></span>
      <svg data-accordion-icon class="w-3 h-3 shrink-0 rotate-180" fill="none" viewBox="0 0 10 6" aria-hidden="true">
        <path stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5 5 1 1 5"/>
      </svg>
    </button>
  </h2>
  <div id="<?= e($panel['id']) ?>-body" class="hidden" aria-labelledby="<?= e($panel['id']) ?>-heading">
    <div class="p-5 bg-white border border-t-0 border-gray-200 <?= $last ? 'rounded-b-lg' : '' ?> dark:bg-gray-800 dark:border-gray-700">
<?php if ($panel['id'] === 'rejected'): ?>
      <ul class="space-y-2 text-sm text-gray-600 dark:text-gray-300">
<?php foreach ($rejected as $item): ?>
        <li>
<?php if (is_array($item)): ?>
          <span class="font-medium text-gray-900 dark:text-white"><?= e($item['ticker'] ?? '—') ?></span>
          <span><?= e($item['reason'] ?? '') ?></span>
<?php else: ?>
          <span><?= e((string)$item) ?></span>
<?php endif; ?>
        </li>
<?php endforeach; ?>
      </ul>
<?php else: ?>
      <ul class="space-y-2 text-sm text-gray-600 dark:text-gray-300">
<?php foreach ($checklist as $item): ?>
        <li class="flex gap-2">
          <span aria-hidden="true" class="text-green-600 dark:text-green-400">✓</span>
          <span><?= e(is_array($item) ? implode(' · ', array_map('strval', $item)) : (string)$item) ?></span>
        </li>
<?php endforeach; ?>
      </ul>
<?php endif; ?>
    </div>
  </div>
<?php endforeach; ?>
</section>
<?php endif; ?>

<?php if (trim((string)($report['disclaimer'] ?? '')) !== ''): ?>
<p class="text-xs text-gray-500 dark:text-gray-400"><?= e($report['disclaimer']) ?></p>
<?php endif; ?>
